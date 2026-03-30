#!/usr/bin/env python3
"""
claude-code-full-insights - Comprehensive Claude Code usage statistics.

The built-in /insights command misses most of your usage:
- Only scans ~/.claude/projects/ (misses nested projects/projects/)
- Counts tool results as human messages (inflating counts ~7x)
- Analyzes ~12 sessions out of thousands
- Has no concept of automated agent sessions

This script fixes all of that. It scans ALL session data, separates human
messages from tool results, and distinguishes interactive sessions from
automated agent infrastructure.

Usage:
    python3 full_insights.py                  # Last 7 days
    python3 full_insights.py --days=30        # Last 30 days
    python3 full_insights.py --all            # All time
    python3 full_insights.py --no-open        # Don't open browser
    python3 full_insights.py --json           # Output JSON only (no HTML)

Works with: Claude Code CLI, PAI, tmux-based agent setups, any Claude Code workflow.
"""

import json
import os
import sys
import glob
import html as html_mod
import platform
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta

CLAUDE_DIR = os.path.expanduser("~/.claude")
PROJECTS_DIR = os.path.join(CLAUDE_DIR, "projects")
REPORT_PATH = os.path.join(CLAUDE_DIR, "usage-data", "full-report.html")
DATA_PATH = os.path.join(CLAUDE_DIR, "usage-data", "full-insights-data.json")

# Detect username for path stripping
_USERNAME = os.path.basename(os.path.expanduser("~"))


def parse_args():
    days = 7
    no_open = False
    json_only = False
    for arg in sys.argv[1:]:
        if arg.startswith("--days="):
            days = int(arg.split("=")[1])
        elif arg == "--all":
            days = 9999
        elif arg == "--no-open":
            no_open = True
        elif arg == "--json":
            json_only = True
    return days, no_open, json_only


def classify_session(filepath):
    """Classify session: interactive (human), automated (queue-operation), or subagent."""
    if "/subagents/" in filepath:
        return "subagent"
    try:
        with open(filepath) as f:
            first_line = f.readline()
            if not first_line.strip():
                return "empty"
            d = json.loads(first_line)
            if d.get("type") == "queue-operation":
                return "automated"
            return "interactive"
    except Exception:
        return "unknown"


def is_tool_result(content):
    """Check if a 'user' message is actually a tool result (sent by Claude API, not typed by human)."""
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                return True
    elif isinstance(content, str):
        if '"type": "tool_result"' in content or "'type': 'tool_result'" in content:
            return True
    elif isinstance(content, dict):
        if content.get("type") == "tool_result":
            return True
    return False


def analyze_session(filepath):
    """Extract messages, tokens, and model info from a session JSONL file."""
    human_msgs = 0
    tool_results = 0
    asst_msgs = 0
    input_tokens = 0
    output_tokens = 0
    cache_read = 0
    cache_write = 0
    models = defaultdict(int)

    try:
        with open(filepath) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    role = d.get("type", d.get("role", ""))
                    if role == "user":
                        msg = d.get("message", {})
                        content = msg.get("content", "") if isinstance(msg, dict) else msg
                        if is_tool_result(content):
                            tool_results += 1
                        else:
                            human_msgs += 1
                    elif role == "assistant":
                        asst_msgs += 1
                        msg = d.get("message", {})
                        usage = msg.get("usage", {})
                        model = msg.get("model", "unknown")
                        models[model] += 1
                        input_tokens += usage.get("input_tokens", 0)
                        output_tokens += usage.get("output_tokens", 0)
                        cache_read += usage.get("cache_read_input_tokens", 0)
                        cache_write += usage.get("cache_creation_input_tokens", 0)
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass

    return {
        "human_msgs": human_msgs,
        "tool_results": tool_results,
        "asst_msgs": asst_msgs,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "models": dict(models),
    }


def get_project_name(filepath):
    """Extract a readable project name from the session file path."""
    parts = filepath.split("/")
    for p in parts:
        if p.startswith("-Users-") or p.startswith("-home-"):
            # Strip the user prefix dynamically
            name = p
            for prefix in [f"-Users-{_USERNAME}-", f"-home-{_USERNAME}-"]:
                if name.startswith(prefix):
                    name = name[len(prefix):]
                    break
            name = name.replace("-", "/")
            if "/worktrees/" in name:
                name = name.split("/worktrees/")[0] + " (wt)"
            return name
    return "unknown"


def scan_sessions(days_back):
    """Find all session JSONL files, including nested projects/projects/ path."""
    cutoff = datetime.now() - timedelta(days=days_back)
    cutoff_ts = cutoff.timestamp()

    # Scan both direct and nested project directories
    patterns = [
        os.path.join(PROJECTS_DIR, "*", "*.jsonl"),
        os.path.join(PROJECTS_DIR, "*", "subagents", "*.jsonl"),
        os.path.join(PROJECTS_DIR, "projects", "*", "*.jsonl"),
        os.path.join(PROJECTS_DIR, "projects", "*", "subagents", "*.jsonl"),
    ]

    all_files = []
    for pattern in patterns:
        all_files.extend(glob.glob(pattern))

    filtered = []
    for f in all_files:
        try:
            if os.path.getmtime(f) >= cutoff_ts:
                filtered.append(f)
        except OSError:
            continue

    return filtered


def fmt(n):
    """Format large numbers for display."""
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _right_panel_html(data, days):
    """Generate the corrected right panel content. All narrative is data-driven."""
    i = data["interactive"]
    a = data["automated"]
    s = data["subagent"]
    days_d = max(days, 1) if days != 9999 else 1

    total_input = i["input_tokens"] + a["input_tokens"] + s["input_tokens"]
    total_output = i["output_tokens"] + a["output_tokens"] + s["output_tokens"]
    total_cache_read = i["cache_read"] + a["cache_read"] + s["cache_read"]
    total_cache_write = i["cache_write"] + a["cache_write"] + s["cache_write"]

    all_models = defaultdict(int)
    for bucket in [i, a, s]:
        for m, c in bucket["models"].items():
            all_models[m] += c

    # Derived facts for conditional narrative
    has_agents = a["count"] > 10
    agent_ratio = a["count"] / max(i["count"], 1)
    n_projects = len(i.get("projects", []))
    msgs_per_session = i["human_msgs"] // max(i["count"], 1)
    out_in = i["output_tokens"] / max(i["input_tokens"], 1)
    top_model = max(all_models, key=all_models.get) if all_models else "unknown"
    top_model_pct = all_models.get(top_model, 0) * 100 // max(sum(all_models.values()), 1)
    uses_single_model = top_model_pct > 90
    cache_ratio = total_cache_read / max(total_input + total_output, 1)
    i_per_day = f"{i['human_msgs'] / days_d:,.0f}" if days != 9999 else "N/A"
    a_sess_day = f"{a['count'] / days_d:,.0f}" if days != 9999 else "N/A"

    def ptable(projects, n=12, color="#2563eb", compact=False):
        if not projects: return ""
        rows = ""
        mx = projects[0][1]["count"] if projects else 1
        for proj, d in projects[:n]:
            w = min(d["count"] / max(mx, 1) * 100, 100)
            if compact:
                rows += f'<div class="R-crow"><span class="R-anm">{html_mod.escape(proj)}</span><span class="R-muted">{d["count"]} sess &middot; {d["human_msgs"]:,} msgs &middot; {fmt(d.get("tokens",0))}</span><div class="R-abt"><div class="R-abf" style="width:{w}%;background:{color}"></div></div></div>'
            else:
                rows += f'<div class="R-area"><div class="R-ahd"><span class="R-anm">{html_mod.escape(proj)}</span><span class="R-act">{d["count"]} sessions</span></div><div class="R-ast">{d["human_msgs"]:,} messages &middot; {fmt(d.get("tokens",0))} tokens</div><div class="R-abt"><div class="R-abf" style="width:{w}%;background:{color}"></div></div></div>'
        return rows

    def mtable():
        rows = ""
        for model, count in sorted(all_models.items(), key=lambda x: -x[1]):
            rows += f'<div class="R-mrow"><span>{html_mod.escape(model)}</span><span class="R-muted">{count:,}</span></div>'
        return rows

    # === BUILD CONDITIONAL NARRATIVE BLOCKS ===

    # At a Glance: What's working
    if has_agents and agent_ratio > 50:
        glance_working = f"You're running a personal AI infrastructure: {i['count']} interactive sessions backed by {a['count']:,} automated agent sessions across {n_projects}+ projects. Your agents outnumber your manual sessions {agent_ratio:.0f}:1."
    elif has_agents:
        glance_working = f"You're using Claude Code across {n_projects}+ projects with {i['count']} interactive sessions and {a['count']:,} automated sessions running alongside. You average ~{msgs_per_session} messages per session."
    elif n_projects > 5:
        glance_working = f"You're active across {n_projects}+ projects with {i['count']} sessions and {i['human_msgs']:,} messages. You average ~{msgs_per_session} messages per session, context-switching across diverse codebases."
    else:
        glance_working = f"You've had {i['count']} sessions with {i['human_msgs']:,} messages across {n_projects} project{'s' if n_projects != 1 else ''}. You average ~{msgs_per_session} messages per session."

    # At a Glance: What's hindering
    glance_hindering_parts = []
    if i["tool_results"] > i["human_msgs"] * 2:
        glance_hindering_parts.append(f"/insights counted {i['tool_results']:,} tool results as your messages, inflating your count {i['tool_results'] // max(i['human_msgs'], 1)}x")
    if data["nested_count"] > 0:
        glance_hindering_parts.append(f"{data['nested_count']:,} sessions ({data['nested_pct']:.0f}%) were invisible to /insights due to data migration paths")
    if not glance_hindering_parts:
        glance_hindering_parts.append("/insights only analyzed ~12 sessions, missing the full picture")
    glance_hindering = ". ".join(glance_hindering_parts) + "."

    # At a Glance: What to try
    glance_try_parts = []
    if has_agents and uses_single_model and "opus" in top_model.lower():
        glance_try_parts.append(f"Route your {a['count']:,} automated sessions through Haiku instead of Opus")
    if n_projects > 5:
        glance_try_parts.append("Set token budgets per project to allocate resources intentionally")
    if has_agents:
        glance_try_parts.append("Track which agent sessions find real work vs idle heartbeats")
    if not glance_try_parts:
        glance_try_parts.append("Review your project token allocation to ensure effort matches priorities")
    glance_try = ". ".join(glance_try_parts) + "."

    # How You Use CC narrative
    usage_paras = []
    usage_paras.append(f"With {i['count']} interactive sessions generating {i['human_msgs']:,} human messages ({i_per_day}/day), you average ~{msgs_per_session} messages per session.")
    if has_agents:
        usage_paras.append(f"Behind your interactive work, <strong>{a['count']:,} automated sessions</strong> run continuously ({a_sess_day}/day), generating {agent_ratio:.0f}x more sessions than you do manually.")
    if out_in > 3:
        usage_paras.append(f"Your sessions are <strong>output-heavy</strong> ({out_in:.1f}x more output than input), meaning Claude generates {out_in:.1f}x more text than it reads per interaction.")
    elif out_in < 0.5:
        usage_paras.append(f"Your sessions are <strong>input-heavy</strong> ({1/out_in:.1f}x more input than output). Claude reads more than it generates per interaction.")
    if uses_single_model:
        usage_paras.append(f"You use <strong>{top_model}</strong> for {top_model_pct}% of responses.")
    elif len(all_models) > 2:
        usage_paras.append(f"You use {len(all_models)} different models, with {top_model} handling {top_model_pct}% of responses.")

    # Key pattern
    if has_agents and agent_ratio > 10:
        key_pattern = f"One human orchestrating an AI workforce. For every message you type, your infrastructure runs {a['count'] // max(i['human_msgs'], 1)} automated sessions."
    elif n_projects > 5:
        key_pattern = f"Active across {n_projects}+ codebases with an average of {msgs_per_session} messages per session."
    else:
        key_pattern = f"{i['human_msgs']:,} messages across {n_projects} project{'s' if n_projects != 1 else ''}. /insights would report {i['human_msgs'] + i['tool_results']:,} by including tool results."

    # Impressive Things
    wins = []
    if has_agents and a["count"] > 1000:
        wins.append(("Large-Scale Agent Infrastructure", f"{a['count']:,} automated sessions across {len(a.get('projects', []))}+ projects, running {a['count'] // max(days_d, 1):,} sessions/day."))
    if n_projects > 8:
        wins.append(("Multi-Domain Context Switching", f"{i['count']} sessions spanning {n_projects}+ distinct projects. ~{msgs_per_session} messages per session across {n_projects} codebases."))
    elif n_projects > 3:
        wins.append(("Multi-Project Workflow", f"Active across {n_projects}+ projects with {i['count']} sessions and {i['human_msgs']:,} messages."))
    if i["human_msgs"] > 1000:
        wins.append(("High-Volume Usage", f"{i['human_msgs']:,} human messages ({i_per_day}/day). /insights would report {i['human_msgs'] + i['tool_results']:,} by including tool results."))
    if cache_ratio > 10:
        wins.append(("High Cache Reuse", f"Cache reads ({fmt(total_cache_read)}) are {total_cache_read // max(total_input + total_output, 1)}x your direct token usage ({fmt(total_input + total_output)})."))
    if not wins:
        wins.append(("Active Claude Code Usage", f"{i['count']} sessions with {i['human_msgs']:,} messages ({i_per_day}/day). /insights would show {i['human_msgs'] + i['tool_results']:,} by counting tool results as yours."))

    # Friction
    frictions = []
    if i["tool_results"] > i["human_msgs"] * 3:
        frictions.append(("Message Count Inflation", f"/insights counted {i['tool_results']:,} tool results as your messages. Your actual human message count is {i['human_msgs']:,}, a {i['tool_results'] // max(i['human_msgs'], 1)}x difference."))
    if data["nested_count"] > 100:
        frictions.append(("Missing Session Data", f"{data['nested_count']:,} sessions ({data['nested_pct']:.0f}% of total) were in a nested path invisible to /insights. These are likely from before a Claude Code data migration."))
    if has_agents and uses_single_model and "opus" in top_model.lower():
        frictions.append(("Agent Cost Opacity", f"{a['count']:,} automated sessions consuming {fmt(a['input_tokens'])} input + {fmt(a['output_tokens'])} output tokens, almost all on {top_model}. Many of these may be idle heartbeats that don't need the most capable model."))
    frictions.append(("Limited /insights Analysis", f"/insights deeply analyzed ~12 sessions out of {i['count'] + a['count']:,}. Any insights based on that sample may not represent your actual usage patterns."))

    # Features to try
    features = []
    if has_agents and uses_single_model and "opus" in top_model.lower():
        features.append(("Model Routing for Agents", f"Route your {a['count']:,} automated sessions through a smaller model. Most agent heartbeats just check for assignments and don't need {top_model}-level reasoning."))
    if has_agents:
        features.append(("Agent Effectiveness Tracking", f"Track what percentage of your {a['count']:,} automated sessions find real work vs idle check-ins. Optimize heartbeat frequency based on actual arrival rates."))
    if n_projects > 5:
        top_proj_pct = (i['projects'][0][1]['tokens'] * 100 // max(i['input_tokens'] + i['output_tokens'], 1)) if i['projects'] and (i['input_tokens'] + i['output_tokens']) > 0 else 0
        features.append(("Token Budget Per Project", f"Your top project uses {top_proj_pct}% of interactive tokens. The remaining {100 - top_proj_pct}% is spread across {n_projects - 1}+ other projects."))
    if i["count"] > 10:
        features.append(("Post-Edit Validation Hooks", f"With {i['count']} sessions, auto-running type checks after edits would catch recurring bugs before they cascade into debugging."))

    # Horizon
    horizons = []
    if has_agents:
        horizons.append(("Intelligent Agent Scheduling", f"Instead of {a['count']:,} fixed-interval heartbeats, agents could predict when assignments are likely and only check during high-probability windows."))
    if uses_single_model and len(all_models) < 3:
        horizons.append(("Cost-Aware Model Selection", f"Currently {top_model_pct}% of responses use {top_model}. Routing lower-complexity tasks to smaller models could reduce costs while maintaining quality where it matters."))
    if n_projects > 5:
        horizons.append(("Cross-Project Knowledge", f"You work across {n_projects}+ projects. Patterns learned in one codebase (fixes, configs) could be shared across others to avoid re-discovering them."))
    if not horizons:
        horizons.append(("Better Usage Insights", "As your usage grows, trends will emerge. Running /better-insights regularly builds a picture of how your Claude Code usage evolves over time."))

    return f"""
<!-- AT A GLANCE -->
<div class="R-glance" id="rs-glance">
  <div class="R-glance-t">At a Glance (Corrected)</div>
  <p><strong>What's working:</strong> {glance_working}</p>
  <p><strong>What's hindering you:</strong> {glance_hindering}</p>
  <p><strong>What to try:</strong> {glance_try}</p>
</div>

<!-- STATS -->
<div class="R-stats-row" id="rs-stats">
  <div class="R-stat"><div class="R-sv">{i['human_msgs']:,}</div><div class="R-sl">Messages</div></div>
  <div class="R-stat"><div class="R-sv">{i['count']}</div><div class="R-sl">Sessions</div></div>
  <div class="R-stat"><div class="R-sv">{fmt(total_input + total_output)}</div><div class="R-sl">Tokens</div></div>
  <div class="R-stat"><div class="R-sv">{days if days != 9999 else 'all'}</div><div class="R-sl">Days</div></div>
  <div class="R-stat"><div class="R-sv">{i_per_day}</div><div class="R-sl">Msgs/Day</div></div>
</div>

<!-- WHAT YOU WORK ON -->
<div class="R-sec" id="rs-work">
  <h3 class="R-h2">What You Work On</h3>
  <p class="R-muted" style="margin-bottom:12px">Based on {i['count']} interactive sessions (vs 12 in original). Your top projects by session count, with token consumption:</p>
  {ptable(i['projects'], compact=True)}
  <div class="R-narrative" style="margin-top:16px">
    <p>Your top project consumes {fmt(i['projects'][0][1]['tokens']) if i['projects'] else '0'} tokens across {i['projects'][0][1]['count'] if i['projects'] else 0} sessions. You work across {len(i.get('projects', []))}+ distinct codebases, averaging ~{i['human_msgs']//max(i['count'],1)} messages per session.</p>
  </div>
</div>

<!-- HOW YOU USE CLAUDE CODE -->
<div class="R-sec" id="rs-usage">
  <h3 class="R-h2">How You Use Claude Code</h3>
  <div class="R-narrative">
    {"".join(f"<p>{p}</p>" for p in usage_paras)}
    <div class="R-key"><strong>Key pattern:</strong> {key_pattern}</div>
  </div>
</div>

<!-- TOKEN USAGE -->
<div class="R-sec" id="rs-tokens">
  <h3 class="R-h2">Token Usage</h3>
  <div class="R-tg">
    <div class="R-tc"><div class="R-tl">Input</div><div class="R-tv" style="color:#2563eb">{fmt(total_input)}</div></div>
    <div class="R-tc"><div class="R-tl">Output</div><div class="R-tv" style="color:#16a34a">{fmt(total_output)}</div></div>
    <div class="R-tc"><div class="R-tl">Cache Read</div><div class="R-tv" style="color:#0891b2">{fmt(total_cache_read)}</div></div>
    <div class="R-tc"><div class="R-tl">Out/In Ratio</div><div class="R-tv" style="color:#7c3aed">{out_in:.1f}x</div></div>
  </div>
  <h4 class="R-muted" style="font-size:.85rem;margin-bottom:6px">Models Used</h4>
  {mtable()}
  <div class="R-narrative" style="margin-top:12px">
    <p>Your interactive sessions consume {fmt(i['input_tokens'])} input and {fmt(i['output_tokens'])} output tokens. Agent infrastructure adds {fmt(a['input_tokens'])} input and {fmt(a['output_tokens'])} output. Cache reads total {fmt(total_cache_read)}, {total_cache_read // max(total_input + total_output, 1)}x the direct token usage.</p>
  </div>
</div>

<!-- IMPRESSIVE THINGS -->
<div class="R-sec" id="rs-wins">
  <h3 class="R-h2">Impressive Things You Did</h3>
  {"".join(f'<div class="R-win"><div class="R-win-t">{t}</div><div class="R-win-d">{d}</div></div>' for t, d in wins)}
</div>

<!-- AGENT INFRASTRUCTURE -->
<div class="R-sec" id="rs-agents">
  <h3 class="R-h2">Agent Infrastructure</h3>
  <p class="R-muted" style="margin-bottom:12px">{a['count']:,} automated sessions ({a_sess_day}/day). These are heartbeats, queue operations, and autonomous agent cycles running without your direct involvement:</p>
  {ptable(a['projects'], color="#d97706", compact=True)}
</div>

<!-- WHERE THINGS GO WRONG -->
<div class="R-sec" id="rs-friction">
  <h3 class="R-h2">Where Things Go Wrong</h3>
  {"".join(f'<div class="R-friction"><div class="R-friction-t">{t}</div><div class="R-friction-d">{d}</div></div>' for t, d in frictions)}
</div>

<!-- FEATURES TO TRY -->
<div class="R-sec" id="rs-features">
  <h3 class="R-h2">Features to Try</h3>
  {"".join(f'<div class="R-feat"><div class="R-feat-t">{t}</div><div class="R-feat-d">{d}</div></div>' for t, d in features)}
</div>

<!-- WHAT /INSIGHTS GETS WRONG -->
<div class="R-sec" id="rs-fixes">
  <h3 class="R-h2">What /insights Gets Wrong</h3>
  <div class="R-fix"><span class="R-old">Counts tool results as your messages</span> &rarr; <span class="R-new">{i['tool_results']:,} tool results excluded. {i['human_msgs']:,} actual human messages.</span></div>
  <div class="R-fix"><span class="R-old">Analyzes ~12 sessions</span> &rarr; <span class="R-new">Scanned {i['count'] + a['count'] + s['count']:,} total sessions.</span></div>
  {"" if data['nested_count'] == 0 else f'<div class="R-fix"><span class="R-old">Misses migrated sessions</span> &rarr; <span class="R-new">{data["nested_count"]:,} sessions recovered ({data["nested_pct"]:.0f}% were invisible).</span></div>'}
  {"" if not has_agents else f'<div class="R-fix"><span class="R-old">No agent awareness</span> &rarr; <span class="R-new">{i["count"]} interactive + {a["count"]:,} automated separated.</span></div>'}
</div>

<!-- ON THE HORIZON -->
<div class="R-sec" id="rs-horizon">
  <h3 class="R-h2">On the Horizon</h3>
  {"".join(f'<div class="R-horizon"><div class="R-horizon-t">{t}</div><div class="R-horizon-d">{d}</div></div>' for t, d in horizons)}
</div>

<!-- FUN ENDING -->
<div class="R-fun">
  <div class="R-fun-h">{i['human_msgs']:,} real messages vs {i['human_msgs'] + i['tool_results']:,} that /insights counted</div>
  <div class="R-fun-d">{"Your agents ran " + str(a['count']) + " sessions on top of that." if has_agents else "Now you know the real numbers."}</div>
</div>
"""


def generate_html(data, days):
    """Generate split-view HTML with original /insights embedded directly (no iframe) for scroll syncing."""
    import re
    period = "all time" if days == 9999 else f"last {days} days"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    i = data["interactive"]
    a = data["automated"]
    days_d = max(days, 1) if days != 9999 else 1
    i_per_day = f"{i['human_msgs'] / days_d:,.0f}" if days != 9999 else "N/A"

    right_content = _right_panel_html(data, days)

    # Check for original /insights report
    original_path = os.path.join(CLAUDE_DIR, "usage-data", "report.html")
    has_original = os.path.exists(original_path)

    if has_original:
        with open(original_path) as f:
            orig = f.read()
        body_m = re.search(r'<body>(.*?)</body>', orig, re.DOTALL)
        style_m = re.search(r'(<style>.*?</style>)', orig, re.DOTALL)
        orig_body = (style_m.group(1) if style_m else "") + (body_m.group(1) if body_m else "")
        stats_m = re.search(r'(\d+)\s*messages.*?(\d+)\s*sessions', orig)
        orig_label = f"{stats_m.group(1)} messages &middot; {stats_m.group(2)} sessions" if stats_m else "limited data"
    else:
        # Generate a synthetic "what /insights would show" left panel
        inflated_msgs = i["human_msgs"] + i["tool_results"]
        direct_sessions = data["direct_count"]
        orig_body = f"""<style>
.si-wrap{{padding:24px;font-family:'Inter',-apple-system,sans-serif;color:#334155;line-height:1.6;max-width:100%}}
.si-wrap h1{{font-size:28px;font-weight:700;color:#0f172a;margin-bottom:6px}}
.si-wrap .si-sub{{color:#64748b;font-size:14px;margin-bottom:24px}}
.si-stats{{display:flex;gap:24px;padding:16px 0;border-top:1px solid #e2e8f0;border-bottom:1px solid #e2e8f0;margin-bottom:24px;flex-wrap:wrap}}
.si-stat{{text-align:center}}.si-sv{{font-size:24px;font-weight:700;color:#0f172a}}.si-sl{{font-size:11px;color:#64748b;text-transform:uppercase}}
.si-note{{background:#fef3c7;border:1px solid #f59e0b;border-radius:12px;padding:18px 22px;margin-bottom:24px}}
.si-note-t{{font-size:15px;font-weight:700;color:#92400e;margin-bottom:10px}}
.si-note p{{font-size:13px;color:#78350f;line-height:1.6;margin-bottom:6px}}
.si-issue{{background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;padding:12px 16px;margin-bottom:8px;font-size:13px;color:#991b1b}}
.si-issue strong{{color:#7f1d1d}}
</style>
<div class="si-wrap">
<h1>What /insights Would Report</h1>
<p class="si-sub">Simulated based on the same data, using /insights methodology</p>
<div class="si-stats">
  <div class="si-stat"><div class="si-sv">{inflated_msgs:,}</div><div class="si-sl">Messages</div></div>
  <div class="si-stat"><div class="si-sv">{direct_sessions:,}</div><div class="si-sl">Sessions</div></div>
  <div class="si-stat"><div class="si-sv">{days if days != 9999 else '?'}</div><div class="si-sl">Days</div></div>
  <div class="si-stat"><div class="si-sv">{inflated_msgs // max(days_d, 1):,}</div><div class="si-sl">Msgs/Day</div></div>
</div>
<div class="si-note">
  <div class="si-note-t">Why These Numbers Are Wrong</div>
  <p>The stats above simulate what /insights would report. Here's what it gets wrong:</p>
</div>
<div class="si-issue"><strong>Message inflation:</strong> /insights counts tool results (API responses sent as "user" role) as your messages. {i['tool_results']:,} tool results are mixed in with your {i['human_msgs']:,} actual messages, inflating the count to {inflated_msgs:,}.</div>
{"" if data['nested_count'] == 0 else f'<div class="si-issue"><strong>Missing sessions:</strong> {data["nested_count"]:,} sessions ({data["nested_pct"]:.0f}%) are stored in a nested path that /insights does not scan. Only {direct_sessions:,} of {direct_sessions + data["nested_count"]:,} total sessions would be visible.</div>'}
{"" if a['count'] < 10 else f'<div class="si-issue"><strong>No agent separation:</strong> {a["count"]:,} automated agent sessions are mixed in with your {i["count"]} interactive sessions. /insights has no concept of automated vs human-initiated sessions.</div>'}
<div class="si-issue"><strong>Sample size:</strong> /insights deeply analyzes ~12 sessions out of {i['count'] + a['count']:,}. The AI-generated narrative, friction analysis, and recommendations are based on this tiny sample.</div>
<div class="si-issue"><strong>No token reporting:</strong> /insights does not report input/output tokens, cache usage, or model breakdown. You have no visibility into compute costs.</div>
</div>"""
        orig_label = f"{inflated_msgs:,} messages (inflated)"
        has_original = True  # We have synthetic content to show

    # Section mapping for synced scrolling: nav key -> [left section id, right section id]
    section_map_js = """{
      'glance': [null, 'rs-glance'],
      'work': ['section-work', 'rs-work'],
      'usage': ['section-usage', 'rs-usage'],
      'tokens': [null, 'rs-tokens'],
      'wins': ['section-wins', 'rs-wins'],
      'friction': ['section-friction', 'rs-friction'],
      'features': ['section-features', 'rs-features'],
      'patterns': ['section-patterns', 'rs-patterns'],
      'fixes': [null, 'rs-fixes'],
      'horizon': ['section-horizon', 'rs-horizon'],
    }"""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Better Insights - {period}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
/* === LAYOUT === */
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;background:#f0f4f8;color:#334155;line-height:1.6}}
.top{{background:linear-gradient(135deg,#ecfdf5,#d1fae5);border-bottom:2px solid #6ee7b7;text-align:center;padding:.75rem 1rem}}
.top h1{{font-size:1.3rem;font-weight:700;color:#065f46}}
.top h1 .cmd{{color:#16a34a;font-family:monospace;font-weight:800}}
.top .sub{{color:#047857;font-size:.78rem;margin-top:.15rem}}
.top .toggle{{margin-top:.4rem}}
.top .toggle button{{font-size:.72rem;padding:3px 12px;border-radius:6px;border:1px solid #6ee7b7;background:rgba(6,95,70,.08);color:#065f46;cursor:pointer;margin:0 2px}}
.top .toggle button:hover{{background:rgba(6,95,70,.18)}}
.top .toggle button.active{{background:#065f46;color:#fff}}
.nav-row{{display:flex;flex-wrap:wrap;gap:4px;justify-content:center;margin-top:.4rem}}
.nav-row a{{font-size:.68rem;color:#065f46;text-decoration:none;padding:2px 8px;border-radius:5px;background:rgba(6,95,70,.08);cursor:pointer}}
.nav-row a:hover{{background:rgba(6,95,70,.18)}}
.split{{display:grid;grid-template-columns:1fr 1fr}}
.split.hide-left{{grid-template-columns:0fr 1fr}}
.split.hide-left .pl{{overflow:hidden;min-width:0;opacity:0;padding:0}}
.panel{{overflow-y:auto;height:calc(100vh - 105px)}}
.pl{{background:#f8fafc;border-right:3px solid #e2e8f0;transition:all .3s}}
.pl .lb{{position:sticky;top:0;z-index:10;background:#fef2f2;color:#991b1b;text-align:center;padding:.3rem;font-weight:600;font-size:.72rem;border-bottom:2px solid #fca5a5}}
.pl .lb .s{{text-decoration:line-through;opacity:.6}}
/* Scope original report styles to left panel */
.pl .ow{{opacity:.85;padding:24px}}
.pl .ow .container{{max-width:100%}}
.pr{{background:#f0f4f8}}
.pr .lb{{position:sticky;top:0;z-index:10;background:#ecfdf5;color:#065f46;text-align:center;padding:.3rem;font-weight:600;font-size:.72rem;border-bottom:2px solid #6ee7b7}}

/* === OVERRIDE leaked original styles === */
.pr,.pr *{{box-sizing:border-box}}
.pr .R-wrap{{padding:24px 32px !important;max-width:100%}}
.pr h3,.pr h4{{font-size:inherit;margin:0}}
/* === RIGHT PANEL (all prefixed R- to avoid conflicts) === */
.R-muted{{color:#64748b;font-size:13px}}
.R-glance{{background:linear-gradient(135deg,#ecfdf5 0%,#d1fae5 100%);border:1px solid #6ee7b7;border-radius:12px;padding:18px 22px;margin-bottom:24px}}
.R-glance-t{{font-size:15px;font-weight:700;color:#065f46;margin-bottom:10px}}
.R-glance p{{font-size:13px;color:#064e3b;line-height:1.6;margin-bottom:8px}}
.R-glance p strong{{color:#047857}}
.R-stats-row{{display:flex;gap:20px;margin-bottom:28px;padding:16px 0;border-top:1px solid #e2e8f0;border-bottom:1px solid #e2e8f0;flex-wrap:wrap}}
.R-stat{{text-align:center;flex:1;min-width:60px}}
.R-sv{{font-size:22px;font-weight:700;color:#0f172a}}
.R-sl{{font-size:10px;color:#64748b;text-transform:uppercase}}
.R-sec{{margin-bottom:28px}}
.R-h2{{font-size:18px;font-weight:600;color:#0f172a;margin-bottom:14px}}
.R-area{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:14px;margin-bottom:8px}}
.R-ahd{{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}}
.R-anm{{font-weight:600;font-size:14px;color:#0f172a}}
.R-act{{font-size:11px;color:#64748b;background:#f1f5f9;padding:2px 8px;border-radius:4px}}
.R-ast{{font-size:12px;color:#475569;margin-bottom:5px}}
.R-abt{{height:5px;background:#f1f5f9;border-radius:3px}}
.R-abf{{height:100%;border-radius:3px;min-width:2px}}
.R-narrative{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:18px;margin-bottom:16px}}
.R-narrative p{{margin-bottom:10px;font-size:13px;color:#475569;line-height:1.65}}
.R-key{{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px 14px;margin-top:10px;font-size:13px;color:#166534}}
.R-tg{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:14px}}
.R-tc{{background:#fff;border:1px solid #e2e8f0;border-radius:6px;padding:10px;text-align:center}}
.R-tl{{font-size:10px;color:#64748b;text-transform:uppercase}}
.R-tv{{font-size:18px;font-weight:700;margin-top:2px}}
.R-mrow{{display:flex;justify-content:space-between;padding:5px 0;font-size:13px;border-bottom:1px solid #f1f5f9}}
.R-win{{background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:14px;margin-bottom:10px}}
.R-win-t{{font-weight:600;font-size:14px;color:#166534;margin-bottom:5px}}
.R-win-d{{font-size:13px;color:#15803d;line-height:1.5}}
.R-friction{{background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;padding:14px;margin-bottom:10px}}
.R-friction-t{{font-weight:600;font-size:14px;color:#991b1b;margin-bottom:5px}}
.R-friction-d{{font-size:13px;color:#7f1d1d;line-height:1.5}}
.R-feat{{background:#f0fdf4;border:1px solid #86efac;border-radius:8px;padding:14px;margin-bottom:10px}}
.R-feat-t{{font-weight:600;font-size:14px;color:#0f172a;margin-bottom:4px}}
.R-feat-d{{font-size:13px;color:#334155;line-height:1.5}}
.R-fix{{background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px 14px;margin-bottom:8px;font-size:13px}}
.R-old{{color:#dc2626;text-decoration:line-through}}
.R-new{{color:#16a34a;font-weight:600}}
.R-horizon{{background:linear-gradient(135deg,#faf5ff 0%,#f5f3ff 100%);border:1px solid #c4b5fd;border-radius:8px;padding:14px;margin-bottom:10px}}
.R-horizon-t{{font-weight:600;font-size:14px;color:#5b21b6;margin-bottom:5px}}
.R-horizon-d{{font-size:13px;color:#334155;line-height:1.5}}
.R-fun{{background:linear-gradient(135deg,#ecfdf5,#d1fae5);border:1px solid #6ee7b7;border-radius:12px;padding:20px;text-align:center;margin-top:24px}}
.R-crow{{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:6px 0;border-bottom:1px solid #f1f5f9}}
.R-crow .R-anm{{font-weight:600;font-size:13px;color:#0f172a}}
.R-crow .R-abt{{flex-basis:100%;height:4px;background:#f1f5f9;border-radius:2px}}
.R-fun-h{{font-size:15px;font-weight:600;color:#065f46;margin-bottom:6px}}
.R-fun-d{{font-size:13px;color:#047857}}
@media(max-width:1024px){{.split{{grid-template-columns:1fr!important}}.pl{{display:none}}.R-tg{{grid-template-columns:repeat(2,1fr)}}}}
</style></head>
<body>
<div class="top">
  <h1><span class="cmd">/better-insights</span> for Claude Code</h1>
  <div class="sub">Original /insights ({orig_label}) vs corrected ({i['human_msgs']:,} messages, {i['count']} sessions, {i_per_day} msgs/day)</div>
  <div class="toggle">
    <button class="active" onclick="setView('split')">Split View</button>
    <button onclick="setView('corrected')">Corrected Only</button>
  </div>
  <div class="nav-row">
    <a onclick="syncScroll('work')">What You Work On</a>
    <a onclick="syncScroll('usage')">How You Use CC</a>
    <a onclick="syncScroll('tokens')">Token Usage</a>
    <a onclick="syncScroll('wins')">Impressive Things</a>
    <a onclick="syncScroll('friction')">Where Things Go Wrong</a>
    <a onclick="syncScroll('features')">Features to Try</a>
    <a onclick="syncScroll('patterns')">New Patterns</a>
    <a onclick="syncScroll('fixes')">What's Wrong</a>
    <a onclick="syncScroll('horizon')">On the Horizon</a>
  </div>
</div>
<div class="split" id="split">
  <div class="panel pl" id="left-panel">
    <div class="lb">ORIGINAL /insights &mdash; <span class="s">{orig_label}</span></div>
    <div class="ow">{orig_body}</div>
  </div>
  <div class="panel pr" id="right-panel">
    <div class="lb">BETTER INSIGHTS &mdash; {i['human_msgs']:,} messages &middot; {i['count']} sessions &middot; {i_per_day} msgs/day</div>
    <div class="R-wrap">{right_content}</div>
  </div>
</div>
<script>
var SM = {section_map_js};

function scrollPanel(panel, elId) {{
  var el = document.getElementById(elId);
  if (!el || !panel) return;
  var lb = panel.querySelector('.lb');
  var stickyH = lb ? lb.offsetHeight : 0;
  var rect = el.getBoundingClientRect();
  var panelRect = panel.getBoundingClientRect();
  panel.scrollTo({{ top: panel.scrollTop + rect.top - panelRect.top - stickyH - 12, behavior: 'smooth' }});
}}

function syncScroll(key) {{
  var map = SM[key];
  if (!map) return;
  var left = document.getElementById('left-panel');
  var right = document.getElementById('right-panel');
  if (map[0] && left) scrollPanel(left, map[0]);
  if (map[1] && right) scrollPanel(right, map[1]);
}}

function setView(mode) {{
  var split = document.getElementById('split');
  var btns = document.querySelectorAll('.toggle button');
  btns.forEach(function(b) {{ b.classList.remove('active'); }});
  if (mode === 'corrected') {{
    split.classList.add('hide-left');
    btns[1].classList.add('active');
  }} else {{
    split.classList.remove('hide-left');
    btns[0].classList.add('active');
  }}
}}

// Intercept original report nav clicks to sync both panels
document.addEventListener('click', function(e) {{
  var link = e.target.closest('a[href^="#section-"]');
  if (link && link.closest('.pl')) {{
    e.preventDefault();
    var hash = link.getAttribute('href').replace('#', '');
    for (var key in SM) {{
      if (SM[key][0] === hash) {{ syncScroll(key); return; }}
    }}
    scrollPanel(document.getElementById('left-panel'), hash);
  }}
}});
</script>
</body></html>"""


def open_file(path):
    """Open a file in the default browser/viewer, cross-platform."""
    system = platform.system()
    if system == "Darwin":
        subprocess.run(["open", path], check=False)
    elif system == "Linux":
        subprocess.run(["xdg-open", path], check=False)
    elif system == "Windows":
        os.startfile(path)


def main():
    days, no_open, json_only = parse_args()
    period_label = "ALL" if days == 9999 else f"last {days} days"
    print(f"Scanning sessions ({period_label})...")

    files = scan_sessions(days)
    total_files = len(files)
    print(f"Found {total_files} session files", file=sys.stderr)

    buckets = {}
    for key in ("interactive", "automated", "subagent"):
        buckets[key] = {
            "count": 0, "human_msgs": 0, "tool_results": 0, "asst_msgs": 0,
            "input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_write": 0,
            "models": defaultdict(int),
            "by_project": defaultdict(lambda: {"count": 0, "human_msgs": 0, "tokens": 0}),
        }

    direct_count = 0
    nested_count = 0

    for idx, f in enumerate(files):
        if (idx + 1) % 1000 == 0:
            print(f"  Processing {idx + 1}/{total_files}...", file=sys.stderr)

        stype = classify_session(f)
        if stype in ("empty", "unknown"):
            stype = "automated"

        stats = analyze_session(f)
        project = get_project_name(f)
        b = buckets[stype]

        b["count"] += 1
        b["human_msgs"] += stats["human_msgs"]
        b["tool_results"] += stats["tool_results"]
        b["asst_msgs"] += stats["asst_msgs"]
        b["input_tokens"] += stats["input_tokens"]
        b["output_tokens"] += stats["output_tokens"]
        b["cache_read"] += stats["cache_read"]
        b["cache_write"] += stats["cache_write"]
        for m, c in stats["models"].items():
            b["models"][m] += c
        b["by_project"][project]["count"] += 1
        b["by_project"][project]["human_msgs"] += stats["human_msgs"]
        b["by_project"][project]["tokens"] += stats["input_tokens"] + stats["output_tokens"]

        if "/projects/projects/" in f:
            nested_count += 1
        else:
            direct_count += 1

    for b in buckets.values():
        b["projects"] = sorted(b["by_project"].items(), key=lambda x: x[1]["count"], reverse=True)

    total_sessions = sum(b["count"] for b in buckets.values())
    data = {
        "interactive": buckets["interactive"],
        "automated": buckets["automated"],
        "subagent": buckets["subagent"],
        "direct_count": direct_count,
        "nested_count": nested_count,
        "nested_pct": (nested_count / max(total_sessions, 1)) * 100,
    }

    i = buckets["interactive"]
    a = buckets["automated"]
    days_d = max(days, 1) if days != 9999 else 1

    # Terminal output
    print()
    print(f"{'=' * 60}")
    print(f"  FULL INSIGHTS ({period_label})")
    print(f"{'=' * 60}")
    print(f"  Your sessions:    {i['count']:>6}  ({i['count'] / days_d:.1f}/day)")
    print(f"  Your messages:    {i['human_msgs']:>6}  ({i['human_msgs'] / days_d:,.0f}/day, excl tool results)")
    print(f"  Tool results:     {i['tool_results']:>6}  (auto-generated, not you)")
    print(f"  Agent sessions:   {a['count']:>6}  ({a['count'] / days_d:,.0f}/day)")
    print(f"  Input tokens:     {fmt(i['input_tokens'] + a['input_tokens']):>6}")
    print(f"  Output tokens:    {fmt(i['output_tokens'] + a['output_tokens']):>6}")
    print(f"  Cache read:       {fmt(i['cache_read'] + a['cache_read']):>6}")
    print(f"{'=' * 60}")

    # Save JSON data
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    summary = {
        "period": period_label,
        "generated_at": datetime.now().isoformat(),
        "interactive": {
            "sessions": i["count"], "human_msgs": i["human_msgs"],
            "tool_results": i["tool_results"], "asst_msgs": i["asst_msgs"],
            "input_tokens": i["input_tokens"], "output_tokens": i["output_tokens"],
            "cache_read": i["cache_read"], "cache_write": i["cache_write"],
            "top_projects": [(p, d) for p, d in i["projects"][:15]],
            "models": dict(i["models"]),
        },
        "automated": {
            "sessions": a["count"], "human_msgs": a["human_msgs"],
            "input_tokens": a["input_tokens"], "output_tokens": a["output_tokens"],
            "top_projects": [(p, d) for p, d in a["projects"][:15]],
        },
        "subagent": {
            "sessions": buckets["subagent"]["count"],
            "messages": buckets["subagent"]["human_msgs"] + buckets["subagent"]["asst_msgs"],
        },
        "data_sources": {
            "direct_sessions": direct_count,
            "nested_sessions": nested_count,
            "nested_pct": round(data["nested_pct"], 1),
        },
    }
    with open(DATA_PATH, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\nJSON: {DATA_PATH}")

    if json_only:
        return

    # Generate HTML report
    report_html = generate_html(data, days)
    with open(REPORT_PATH, "w") as f:
        f.write(report_html)
    print(f"HTML: {REPORT_PATH}")

    if not no_open:
        open_file(REPORT_PATH)
        print("Opened in browser.")


if __name__ == "__main__":
    main()
