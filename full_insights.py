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


def generate_html(data, days):
    """Generate a standalone HTML report."""
    period = "all time" if days == 9999 else f"last {days} days"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    i = data["interactive"]
    a = data["automated"]
    s = data["subagent"]

    total_input = i["input_tokens"] + a["input_tokens"] + s["input_tokens"]
    total_output = i["output_tokens"] + a["output_tokens"] + s["output_tokens"]
    total_cache_read = i["cache_read"] + a["cache_read"] + s["cache_read"]
    total_cache_write = i["cache_write"] + a["cache_write"] + s["cache_write"]

    all_models = defaultdict(int)
    for bucket in [i, a, s]:
        for m, c in bucket["models"].items():
            all_models[m] += c

    def project_table(projects, n=12, bar_color="#2563eb"):
        if not projects:
            return "<p style='color:#64748b'>No sessions</p>"
        rows = ""
        max_count = projects[0][1]["count"] if projects else 1
        for proj, d in projects[:n]:
            w = min(d["count"] / max(max_count, 1) * 100, 100)
            tokens = d.get("tokens", 0)
            rows += f"""<tr>
                <td class="pn">{html_mod.escape(proj)}</td>
                <td class="n">{d['count']:,}</td>
                <td class="n">{d['human_msgs']:,}</td>
                <td class="n">{fmt(tokens)}</td>
                <td class="bc"><div class="b" style="width:{w}%;background:{bar_color}"></div></td>
            </tr>"""
        return f"""<table>
            <tr><th>Project</th><th class="n">Sessions</th><th class="n">Messages</th><th class="n">Tokens</th><th></th></tr>
            {rows}
        </table>"""

    def model_table():
        rows = ""
        for model, count in sorted(all_models.items(), key=lambda x: -x[1]):
            rows += f"<tr><td>{html_mod.escape(model)}</td><td class='n'>{count:,}</td></tr>"
        return rows

    days_d = max(days, 1) if days != 9999 else 1
    i_per_day = f"{i['human_msgs'] / days_d:,.0f}" if days != 9999 else "N/A"
    i_sess_day = f"{i['count'] / days_d:.1f}" if days != 9999 else "N/A"
    a_sess_day = f"{a['count'] / days_d:,.0f}" if days != 9999 else "N/A"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Full Insights - {period}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:#f0f4f8;color:#334155;padding:2rem;line-height:1.5}}
.c{{max-width:960px;margin:0 auto}}
h1{{font-size:1.8rem;font-weight:700;color:#0f172a;margin-bottom:.25rem}}
.sub{{color:#64748b;font-size:.95rem;margin-bottom:2rem}}
.g{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1rem;margin-bottom:2rem}}
.cd{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:1.25rem}}
.cl{{color:#64748b;font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;margin-bottom:.2rem}}
.cv{{font-size:1.5rem;font-weight:700}}
.ct{{color:#64748b;font-size:.75rem;margin-top:.2rem}}
.s{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:1.5rem;margin-bottom:1.5rem}}
.s h2{{font-size:1.1rem;font-weight:600;color:#0f172a;margin-bottom:1rem}}
.badge{{display:inline-block;padding:.1rem .5rem;border-radius:10px;font-size:.7rem;font-weight:600;text-transform:uppercase}}
.bb{{background:rgba(37,99,235,.1);color:#2563eb}}.bo{{background:rgba(217,119,6,.1);color:#d97706}}.bp{{background:rgba(124,58,237,.1);color:#7c3aed}}
table{{width:100%;border-collapse:collapse;font-size:.9rem}}
th{{text-align:left;color:#64748b;font-weight:500;padding:.5rem .75rem;border-bottom:1px solid #e2e8f0;font-size:.75rem;text-transform:uppercase}}
td{{padding:.45rem .75rem;border-bottom:1px solid #f1f5f9}}
.n{{text-align:right;font-variant-numeric:tabular-nums}}
.pn{{max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.bc{{width:100px}}.b{{height:6px;border-radius:3px;min-width:2px}}
tr:hover{{background:#f8fafc}}
.tg{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.75rem;margin-bottom:1rem}}
.tc{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:.75rem 1rem;text-align:center}}
.tl{{color:#64748b;font-size:.7rem;text-transform:uppercase}}.tv{{font-size:1.2rem;font-weight:700;margin-top:.1rem}}.ts{{color:#64748b;font-size:.7rem}}
.cmp{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:1rem 1.25rem;font-size:.9rem}}
.cmp strong{{color:#2563eb}}.cmp .old{{color:#dc2626;text-decoration:line-through;opacity:.6}}
.ft{{text-align:center;color:#64748b;font-size:.8rem;margin-top:2rem;padding-top:1rem;border-top:1px solid #e2e8f0}}
</style></head>
<body><div class="c">
<h1>Full Insights</h1>
<p class="sub">Generated {now} &middot; {period} &middot; {i['count']+a['count']+s['count']:,} total sessions</p>
<div class="g">
  <div class="cd"><div class="cl">Your Sessions</div><div class="cv" style="color:#2563eb">{i['count']:,}</div><div class="ct">{i_sess_day}/day</div></div>
  <div class="cd"><div class="cl">Your Messages</div><div class="cv" style="color:#16a34a">{i['human_msgs']:,}</div><div class="ct">{i_per_day}/day (excl. tool results)</div></div>
  <div class="cd"><div class="cl">Agent Sessions</div><div class="cv" style="color:#d97706">{a['count']:,}</div><div class="ct">{a_sess_day}/day infrastructure</div></div>
  <div class="cd"><div class="cl">Input Tokens</div><div class="cv" style="color:#7c3aed">{fmt(total_input)}</div><div class="ct">You: {fmt(i['input_tokens'])} / Agents: {fmt(a['input_tokens'])}</div></div>
  <div class="cd"><div class="cl">Output Tokens</div><div class="cv" style="color:#0891b2">{fmt(total_output)}</div><div class="ct">You: {fmt(i['output_tokens'])} / Agents: {fmt(a['output_tokens'])}</div></div>
  <div class="cd"><div class="cl">Cache Savings</div><div class="cv" style="color:#16a34a">{fmt(total_cache_read)}</div><div class="ct">tokens read from cache</div></div>
</div>
<div class="s"><h2><span class="badge bb">Interactive</span> Your Usage</h2>{project_table(i['projects'])}</div>
<div class="s"><h2><span class="badge bo">Automated</span> Agent Infrastructure</h2>{project_table(a['projects'], bar_color="#d97706")}</div>
{"" if s['count'] == 0 else f'<div class="s"><h2><span class="badge bp">Subagent</span> Spawned Sessions</h2><p style="color:#64748b">{s["count"]:,} sessions &middot; {s["human_msgs"]+s["asst_msgs"]:,} messages &middot; {fmt(s["input_tokens"]+s["output_tokens"])} tokens</p></div>'}
<div class="s"><h2>Token Usage</h2>
<div class="tg">
  <div class="tc"><div class="tl">Input</div><div class="tv" style="color:#2563eb">{fmt(total_input)}</div><div class="ts">{total_input:,}</div></div>
  <div class="tc"><div class="tl">Output</div><div class="tv" style="color:#16a34a">{fmt(total_output)}</div><div class="ts">{total_output:,}</div></div>
  <div class="tc"><div class="tl">Cache Read</div><div class="tv" style="color:#0891b2">{fmt(total_cache_read)}</div><div class="ts">saved reprocessing</div></div>
  <div class="tc"><div class="tl">Cache Write</div><div class="tv" style="color:#d97706">{fmt(total_cache_write)}</div><div class="ts">stored for reuse</div></div>
</div>
<h3 style="font-size:.85rem;color:#64748b;margin-bottom:.5rem">Models Used</h3>
<table><tr><th>Model</th><th class="n">Responses</th></tr>{model_table()}</table>
</div>
<div class="s"><h2>vs /insights</h2>
<div class="cmp">
  <p><strong>/insights</strong> analyzes ~12 sessions and counts tool results as messages</p>
  <p style="margin-top:.5rem"><strong>Full scan:</strong> {i['human_msgs']:,} human messages across {i['count']:,} interactive + {a['count']:,} automated sessions</p>
  <p style="margin-top:.5rem;color:#64748b">/insights misses {data['nested_count']:,} sessions in nested path ({data['nested_pct']:.0f}% of total)</p>
</div></div>
<div class="ft">claude-code-full-insights &middot; {now}</div>
</div></body></html>"""


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
