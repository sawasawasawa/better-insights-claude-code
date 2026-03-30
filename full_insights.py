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
    """Generate the corrected right panel content."""
    i = data["interactive"]
    a = data["automated"]
    s = data["subagent"]
    period = "all time" if days == 9999 else f"last {days} days"
    days_d = max(days, 1) if days != 9999 else 1

    total_input = i["input_tokens"] + a["input_tokens"] + s["input_tokens"]
    total_output = i["output_tokens"] + a["output_tokens"] + s["output_tokens"]
    total_cache_read = i["cache_read"] + a["cache_read"] + s["cache_read"]
    total_cache_write = i["cache_write"] + a["cache_write"] + s["cache_write"]

    all_models = defaultdict(int)
    for bucket in [i, a, s]:
        for m, c in bucket["models"].items():
            all_models[m] += c

    def ptable(projects, n=12, color="#2563eb"):
        if not projects:
            return "<p class='muted'>No sessions</p>"
        rows = ""
        mx = projects[0][1]["count"] if projects else 1
        for proj, d in projects[:n]:
            w = min(d["count"] / max(mx, 1) * 100, 100)
            rows += f'<div class="rp-area"><div class="rp-hd"><span class="rp-nm">{html_mod.escape(proj)}</span><span class="rp-ct">{d["count"]} sessions</span></div><div class="rp-st">{d["human_msgs"]:,} messages &middot; {fmt(d.get("tokens",0))} tokens</div><div class="rp-bt"><div class="rp-bf" style="width:{w}%;background:{color}"></div></div></div>'
        return rows

    def mtable():
        rows = ""
        for model, count in sorted(all_models.items(), key=lambda x: -x[1]):
            rows += f'<div class="rp-mr"><span>{html_mod.escape(model)}</span><span class="muted">{count:,}</span></div>'
        return rows

    i_per_day = f"{i['human_msgs'] / days_d:,.0f}" if days != 9999 else "N/A"
    i_sess_day = f"{i['count'] / days_d:.1f}" if days != 9999 else "N/A"
    a_sess_day = f"{a['count'] / days_d:,.0f}" if days != 9999 else "N/A"
    out_in = i['output_tokens'] / max(i['input_tokens'], 1)
    tok_per_msg = (i['input_tokens'] + i['output_tokens']) // max(i['human_msgs'], 1)

    return f"""
<div class="rp-stats">
  <div class="rp-stat"><div class="rp-sv">{i['human_msgs']:,}</div><div class="rp-sl">Your Messages</div></div>
  <div class="rp-stat"><div class="rp-sv">{i['count']}</div><div class="rp-sl">Sessions</div></div>
  <div class="rp-stat"><div class="rp-sv">{fmt(total_input + total_output)}</div><div class="rp-sl">Tokens</div></div>
  <div class="rp-stat"><div class="rp-sv">{days if days != 9999 else 'all'}</div><div class="rp-sl">Days</div></div>
  <div class="rp-stat"><div class="rp-sv">{i_per_day}</div><div class="rp-sl">Msgs/Day</div></div>
</div>

<div class="rp-sec" id="rs-work"><h2>What You Work On</h2>
<p class="muted" style="margin-bottom:12px">Based on {i['count']} interactive sessions. Top projects:</p>
{ptable(i['projects'])}
</div>

<div class="rp-sec" id="rs-tokens"><h2>Token Usage</h2>
<div class="rp-tg">
  <div class="rp-tc"><div class="rp-tl">Input</div><div class="rp-tv" style="color:#2563eb">{fmt(total_input)}</div></div>
  <div class="rp-tc"><div class="rp-tl">Output</div><div class="rp-tv" style="color:#16a34a">{fmt(total_output)}</div></div>
  <div class="rp-tc"><div class="rp-tl">Cache Read</div><div class="rp-tv" style="color:#0891b2">{fmt(total_cache_read)}</div></div>
  <div class="rp-tc"><div class="rp-tl">Out/In Ratio</div><div class="rp-tv" style="color:#7c3aed">{out_in:.1f}x</div></div>
</div>
<h3 class="muted" style="font-size:.85rem;margin-bottom:6px">Models Used</h3>
{mtable()}
</div>

<div class="rp-sec" id="rs-agents"><h2>Agent Infrastructure</h2>
<p class="muted" style="margin-bottom:12px">{a['count']:,} automated sessions ({a_sess_day}/day). Top projects:</p>
{ptable(a['projects'], color="#d97706")}
</div>

<div class="rp-sec" id="rs-fixes"><h2>What /insights Gets Wrong</h2>
<div class="rp-fix"><span class="old">Counts tool results as your messages</span> &rarr; <span class="new">{i['tool_results']:,} tool results excluded, only {i['human_msgs']:,} human messages counted</span></div>
<div class="rp-fix"><span class="old">Analyzes ~12 sessions</span> &rarr; <span class="new">Scanned {i['count'] + a['count'] + s['count']:,} sessions ({i['count']} interactive + {a['count']:,} automated)</span></div>
<div class="rp-fix"><span class="old">Misses nested project paths</span> &rarr; <span class="new">{data['nested_count']:,} sessions recovered ({data['nested_pct']:.0f}% of total were invisible)</span></div>
</div>
"""


def generate_html(data, days):
    """Generate split-view HTML if original /insights report exists, otherwise standalone."""
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
        stats_m = re.search(r'(\d+)\s*messages.*?(\d+)\s*sessions', orig)
        orig_label = f"{stats_m.group(1)} messages &middot; {stats_m.group(2)} sessions" if stats_m else "limited data"
    else:
        orig_label = "run /insights first"

    nav_links = """<div class="nav-row">
      <a onclick="scrollTo_('work')">What You Work On</a>
      <a onclick="scrollTo_('tokens')">Token Usage</a>
      <a onclick="scrollTo_('agents')">Agent Infrastructure</a>
      <a onclick="scrollTo_('fixes')">What's Wrong</a>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Better Insights - {period}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;background:#f0f4f8;color:#334155;line-height:1.6}}
.top{{background:linear-gradient(135deg,#ecfdf5,#d1fae5);border-bottom:2px solid #6ee7b7;text-align:center;padding:1rem}}
.top h1{{font-size:1.3rem;font-weight:700;color:#065f46}}
.top h1 .cmd{{color:#16a34a;font-family:monospace;font-weight:800}}
.top .sub{{color:#047857;font-size:.8rem;margin-top:.15rem}}
.top .toggle{{margin-top:.5rem}}
.top .toggle button{{font-size:.75rem;padding:4px 14px;border-radius:6px;border:1px solid #6ee7b7;background:rgba(6,95,70,.08);color:#065f46;cursor:pointer;margin:0 3px}}
.top .toggle button:hover{{background:rgba(6,95,70,.18)}}
.top .toggle button.active{{background:#065f46;color:#fff}}
.nav-row{{display:flex;flex-wrap:wrap;gap:5px;justify-content:center;margin-top:.5rem}}
.nav-row a{{font-size:.7rem;color:#065f46;text-decoration:none;padding:3px 10px;border-radius:5px;background:rgba(6,95,70,.08);cursor:pointer}}
.nav-row a:hover{{background:rgba(6,95,70,.18)}}
.split{{display:grid;grid-template-columns:1fr 1fr}}
.split.hide-left{{grid-template-columns:0fr 1fr}}
.split.hide-left .pl{{overflow:hidden;min-width:0;opacity:0;padding:0}}
.panel{{overflow-y:auto;height:calc(100vh - 110px)}}
.pl{{background:#f8fafc;border-right:3px solid #e2e8f0;transition:all .3s}}
.pl .lb{{position:sticky;top:0;z-index:10;background:#fef2f2;color:#991b1b;text-align:center;padding:.35rem;font-weight:600;font-size:.75rem;border-bottom:2px solid #fca5a5}}
.pl .lb .s{{text-decoration:line-through;opacity:.6}}
.pl .ow{{opacity:.85}}.pl .ow .container{{max-width:100%;padding:24px}}
.pr{{background:#f0f4f8}}
.pr .lb{{position:sticky;top:0;z-index:10;background:#ecfdf5;color:#065f46;text-align:center;padding:.35rem;font-weight:600;font-size:.75rem;border-bottom:2px solid #6ee7b7}}
.rp{{padding:1.5rem}}
/* Right panel styles */
.muted{{color:#64748b;font-size:.85rem}}
.rp-stats{{display:flex;gap:12px;margin-bottom:24px;padding:16px 0;border-top:1px solid #cbd5e1;border-bottom:1px solid #cbd5e1;flex-wrap:wrap}}
.rp-stat{{text-align:center;flex:1;min-width:70px}}
.rp-sv{{font-size:22px;font-weight:700;color:#0f172a}}
.rp-sl{{font-size:11px;color:#64748b;text-transform:uppercase}}
.rp-sec{{margin-bottom:28px}}
.rp-sec h2{{font-size:18px;font-weight:600;color:#0f172a;margin-bottom:12px}}
.rp-area{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px;margin-bottom:8px}}
.rp-hd{{display:flex;justify-content:space-between;align-items:center;margin-bottom:3px}}
.rp-nm{{font-weight:600;font-size:13px;color:#0f172a}}
.rp-ct{{font-size:11px;color:#64748b;background:#f1f5f9;padding:1px 7px;border-radius:4px}}
.rp-st{{font-size:12px;color:#475569;margin-bottom:4px}}
.rp-bt{{height:5px;background:#f1f5f9;border-radius:3px}}
.rp-bf{{height:100%;border-radius:3px;min-width:2px}}
.rp-tg{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px}}
.rp-tc{{background:#fff;border:1px solid #e2e8f0;border-radius:6px;padding:10px;text-align:center}}
.rp-tl{{font-size:10px;color:#64748b;text-transform:uppercase}}
.rp-tv{{font-size:18px;font-weight:700;margin-top:2px}}
.rp-mr{{display:flex;justify-content:space-between;padding:4px 0;font-size:13px;border-bottom:1px solid #f1f5f9}}
.rp-fix{{background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px 14px;margin-bottom:8px;font-size:13px}}
.rp-fix .old{{color:#dc2626;text-decoration:line-through}}
.rp-fix .new{{color:#16a34a;font-weight:600}}
@media(max-width:1024px){{.split{{grid-template-columns:1fr!important}}.pl{{display:none}}.rp-tg{{grid-template-columns:repeat(2,1fr)}}}}
</style></head>
<body>
<div class="top">
  <h1><span class="cmd">/better-insights</span> for Claude Code</h1>
  <div class="sub">Original /insights ({orig_label}) vs corrected ({i['human_msgs']:,} messages, {i['count']} sessions, {i_per_day} msgs/day)</div>
  <div class="toggle">
    <button class="active" onclick="setView('split')">Split View</button>
    <button onclick="setView('corrected')">Corrected Only</button>
  </div>
  {nav_links}
</div>
<div class="split" id="split">
  <div class="panel pl" id="left-panel">
    <div class="lb">ORIGINAL /insights &mdash; <span class="s">{orig_label}</span></div>
    <iframe class="ow" id="orig-frame" srcdoc="" style="width:100%;border:none;height:calc(100vh - 110px)"></iframe>
  </div>
  <div class="panel pr" id="right-panel">
    <div class="lb">BETTER INSIGHTS &mdash; {i['human_msgs']:,} messages &middot; {i['count']} sessions &middot; {i_per_day} msgs/day</div>
    <div class="rp">{right_content}</div>
  </div>
</div>
<script>
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
function scrollTo_(id) {{
  var el = document.getElementById('rs-' + id);
  var panel = document.getElementById('right-panel');
  if (el && panel) {{
    var lb = panel.querySelector('.lb');
    var stickyH = lb ? lb.offsetHeight : 0;
    var rect = el.getBoundingClientRect();
    var panelRect = panel.getBoundingClientRect();
    panel.scrollTo({{ top: panel.scrollTop + rect.top - panelRect.top - stickyH - 10, behavior: 'smooth' }});
  }}
}}
// Load original report into iframe
var frame = document.getElementById('orig-frame');
if (frame) {{
  frame.src = 'report.html';
}}
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
