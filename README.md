# agent-autonomous-prompt-engineer

A Claude Code **skill** plugin. Autonomously engineers, evaluates and iteratively improves prompts, skills and agent definitions — a skill- and agent-based plugin for headless prompt-optimization runs.

This plugin ships **only the skill content** — no binaries, no MCP server.

## Install

```
/plugin marketplace add Seretos/agent-marketplace
/plugin install agent-autonomous-prompt-engineer@agent-marketplace
```

If the skill teaches Claude how to use a specific MCP, declare that MCP as a dependency in `.claude-plugin/plugin.json` (`dependencies` array). Claude Code will install/load it automatically.

## What the skill teaches

See `skills/autonomous-prompt-engineer/SKILL.md` for the full content.
