# agent-autonomous-prompt-engineer

Takes one work package whose change is **prose that a model executes** — skills, agent definitions, prompts, tool documentation, `AGENTS.md` — from a prepared worktree to a pull request with a green CI pipeline, headless and without asking a human. The sibling of `agent-autonomous-developer` for everything no program can test.

## Key features

- **Evidence that fits prose, instead of text-pin tests.** Tier 0 lint (frontmatter, LF-only, referenced files, contract tables vs. the scripts behind them), tier 1 step replay on an input rebuilt from a real past case, tier 2 blind test by a fresh model that sees only the surface. The tier is chosen by a script, never by a model.
- **Baseline vs. after, judged as a delta.** Every scenario runs against the old and the new text in isolated `claude -p` processes (no settings, no MCP, empty working directory, leak check over the transcript); a model-free merge decides. The baseline is cached by artifact hash, so a ticket pays only for "after".
- **Same contract as the developer plugin.** Same headless invocation, same `adev:event` ticket comments and terminal events (`ci-green` / `blocked` / `failed`), checkpoints pushed at every step — `agent-ticket-orchestrator` drives it unchanged.
- **One critic, no loops.** A single isolated scenario-honesty critic (tailored? answer leaked? falsifiable?), hard round caps and a progress-based stagnation stop; a plan-level objection ends in a `blocked` question, never in an endless round trip.
- **Honest about what it did not show.** Every PR carries a "Not covered by tests" section: requirement, tier, reason.
