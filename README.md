# Better Insights for Claude Code

**`/insights` told me I send 66 messages a day. The real number is 844.**

The built-in `/insights` command counts tool results as your messages (inflating counts ~7x), only analyzes ~12 sessions, and misses older sessions stored in a nested path after Claude Code data migrations. This script fixes all of that: scans everything, separates what you typed from what the API generated, reports token usage and model breakdown.

![Better Insights split view - Original vs Corrected](screenshot.png)

## Install as a Skill (recommended)

Run this once to install as `/full-insights` inside Claude Code:

```bash
SKILL_DIR="${HOME}/.claude/skills/FullInsights"
mkdir -p "$SKILL_DIR"
curl -sL https://raw.githubusercontent.com/sawasawasawa/better-insights-claude-code/master/full_insights.py -o "$SKILL_DIR/full_insights.py"
cat > "$SKILL_DIR/SKILL.md" << 'SKILL'
---
name: full-insights
description: Comprehensive Claude Code usage stats. Separates interactive from automated sessions, human messages from tool results. Fixes /insights undercounting.
user_invocable: true
---

# Full Insights

```bash
python3 ~/.claude/skills/FullInsights/full_insights.py "$@"
```
SKILL
echo "Installed. Run /full-insights in Claude Code."
```

Then type `/full-insights` in any Claude Code session.

## Or run directly

```bash
python3 full_insights.py              # Last 7 days (default)
python3 full_insights.py --days=30    # Last 30 days
python3 full_insights.py --all        # All time
python3 full_insights.py --json       # JSON only, no HTML
python3 full_insights.py --no-open    # Don't auto-open browser
```

Zero dependencies. Python 3.6+.

## What it fixes

| Issue | /insights | Better Insights |
|---|---|---|
| Message counting | Counts tool results as human messages (~7x inflation) | Separates human messages from tool results |
| Session coverage | Analyzes ~12 sessions | Scans all sessions |
| Data migration | Misses sessions in `projects/projects/` after upgrades | Scans both paths |
| Agent awareness | No concept of automated sessions | Separates interactive vs automated vs subagent |
| Token usage | Not reported | Input, output, cache read/write breakdown |
| Model tracking | Not reported | Per-model response counts |

## Why /insights undercounts

**Tool result inflation**: In the Claude API, tool execution results are sent as `role: "user"` messages. `/insights` counts these as messages you typed. In practice, ~85% of "user" messages are actually tool results, inflating your count roughly 7x.

**Missing sessions**: Claude Code data migrations (during version upgrades) move older sessions from `~/.claude/projects/` to `~/.claude/projects/projects/`. The `/insights` command only scans the direct path. If you've been using Claude Code for a while, this can mean 80-90% of your sessions are invisible.

**Session sampling**: `/insights` deeply analyzes only ~12 sessions out of thousands, then extrapolates.

## License

MIT
