# claude-code-full-insights

Comprehensive usage statistics for Claude Code that fixes the undercounting in the built-in `/insights` command.

## The Problem

The built-in `/insights` command has several blind spots:

| Issue | /insights | full-insights |
|---|---|---|
| Session discovery | Only scans `~/.claude/projects/` | Also scans nested `projects/projects/` |
| Message counting | Counts tool results as human messages (inflates ~7x) | Separates human messages from tool results |
| Session analysis | Analyzes ~12 sessions | Scans all sessions |
| Agent awareness | No concept of automated sessions | Separates interactive vs automated vs subagent |
| Token usage | Not reported | Full breakdown (input, output, cache read/write) |
| Model tracking | Not reported | Per-model response counts |

## Quick Start

```bash
# Run directly
python3 full_insights.py

# Last 30 days
python3 full_insights.py --days=30

# All time
python3 full_insights.py --all

# JSON output only (no HTML report)
python3 full_insights.py --json

# Don't auto-open browser
python3 full_insights.py --no-open
```

No dependencies. Just Python 3.6+.

## What It Does

1. **Scans all session files** in `~/.claude/projects/` (both direct and nested paths)
2. **Classifies each session** as:
   - **Interactive** - you initiated it, human-typed messages
   - **Automated** - agent heartbeats, queue operations, PAI infrastructure
   - **Subagent** - spawned by other sessions
3. **Separates human messages from tool results** - the Claude API sends tool results as "user" role messages, inflating counts
4. **Extracts token usage** - input, output, cache read, cache write per session
5. **Generates an HTML report** with cards, project tables, and token breakdown
6. **Saves JSON data** for further analysis

## Output

- **Terminal**: Summary stats
- **HTML report**: `~/.claude/usage-data/full-report.html` (auto-opens)
- **JSON data**: `~/.claude/usage-data/full-insights-data.json`

## Install as Claude Code Skill

If you want to run this as `/full-insights` inside Claude Code:

```bash
mkdir -p ~/.claude/skills/FullInsights
cp full_insights.py ~/.claude/skills/FullInsights/
cat > ~/.claude/skills/FullInsights/SKILL.md << 'EOF'
---
name: full-insights
description: Generate comprehensive usage statistics across ALL Claude Code sessions. Separates interactive from automated. Fixes /insights undercounting.
user_invocable: true
---

# Full Insights

```bash
python3 ~/.claude/skills/FullInsights/full_insights.py "$@"
```
EOF
```

## How Sessions Are Classified

| First JSONL line type | Classification |
|---|---|
| `type: "queue-operation"` | Automated (agent heartbeat) |
| `type: "user"` | Interactive (human-initiated) |
| Path contains `/subagents/` | Subagent |
| Empty file | Counted as automated |

## Why /insights Undercounts

Claude Code stores session data in two locations:

```
~/.claude/projects/
  -Users-you-project-name/          # Direct sessions (what /insights sees)
    session-uuid.jsonl
  projects/
    -Users-you-project-name/        # Nested sessions (invisible to /insights)
      session-uuid.jsonl
```

The nested `projects/projects/` path appears to be from older Claude Code versions or migrations. `/insights` only scans the direct path, missing up to 88% of sessions.

Additionally, in the Claude API, tool execution results are sent as `role: "user"` messages. When `/insights` counts "user messages," it includes these tool results, inflating the count by roughly 7x.

## License

MIT
