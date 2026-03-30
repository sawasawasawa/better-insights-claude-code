#!/usr/bin/env node

import { execSync, spawn } from "child_process";
import { existsSync, mkdirSync, writeFileSync, copyFileSync } from "fs";
import { join } from "path";
import { homedir } from "os";
import { fileURLToPath } from "url";
import { dirname } from "path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const args = process.argv.slice(2);
const command = args[0];

const SKILL_DIR = join(homedir(), ".claude", "skills", "FullInsights");
const SCRIPT_SRC = join(__dirname, "full_insights.py");

function install() {
  mkdirSync(SKILL_DIR, { recursive: true });
  copyFileSync(SCRIPT_SRC, join(SKILL_DIR, "full_insights.py"));
  writeFileSync(
    join(SKILL_DIR, "SKILL.md"),
    `---
name: full-insights
description: Comprehensive Claude Code usage stats. Separates interactive from automated sessions, human messages from tool results. Fixes /insights undercounting.
user_invocable: true
---

# Full Insights

\`\`\`bash
python3 ~/.claude/skills/FullInsights/full_insights.py "$@"
\`\`\`
`
  );
  console.log("Installed /full-insights skill to " + SKILL_DIR);
  console.log('Run /full-insights in any Claude Code session.');
}

function run() {
  const pyArgs = args.filter((a) => a !== "run");
  const child = spawn("python3", [SCRIPT_SRC, ...pyArgs], {
    stdio: "inherit",
  });
  child.on("close", (code) => process.exit(code));
}

function help() {
  console.log(`
Better Insights for Claude Code

Usage:
  npx better-insights-claude-code install    Install as /full-insights skill
  npx better-insights-claude-code run        Run directly (default)
  npx better-insights-claude-code --days=30  Run with options
  npx better-insights-claude-code --all      Scan all time
  npx better-insights-claude-code --json     JSON output only

What it fixes:
  - /insights counts tool results as your messages (~7x inflation)
  - /insights only analyzes ~12 sessions
  - /insights misses sessions after Claude Code data migrations
`);
}

if (command === "install") {
  install();
} else if (command === "help" || command === "--help" || command === "-h") {
  help();
} else {
  // Default: run the script, passing all args through
  run();
}
