# agent-autonomous-prompt-engineer

A Claude Code **skill + agents** plugin. It takes **one work package whose change is prose that a model executes** — a skill, an agent definition, a prompt, tool documentation, an `AGENTS.md` — from a prepared worktree to a pull request with a **green CI pipeline**, headless, without asking a human.

It is the sibling of [`agent-autonomous-developer`](https://github.com/Seretos/agent-autonomous-developer): same caller (`agent-ticket-orchestrator`), same invocation, same `adev:event` ticket comments — but evidence that fits prose. Nothing executes a skill file, so every "test" of one is a string comparison; this plugin writes none. No RED phase, no test critic, no test that asserts a phrase exists in a prose file.

No binaries, no MCP server. Scripts need Python ≥ 3.9 and the `claude` CLI; hooks need Node.

## Install

```
/plugin marketplace add Seretos/agent-marketplace
/plugin install agent-autonomous-prompt-engineer@agent-marketplace
```

`agent-project-issues` is declared as a dependency and installed with it.

## Use

Headless, from a worktree on a feature branch (the caller creates both):

```
claude -p "/agent-autonomous-prompt-engineer:process-prompt-engineer package=<id> project_id=<project> worktree_path=<abs path> base_branch=<branch>" --disallowedTools AskUserQuestion
```

Optional: `attempt=<n>`, `consumer_model=<model>` (default `sonnet` — the weakest model the surface is meant for), `samples=<n>` (default 3).

The run posts `<!-- adev:event v1 -->` comments on the package ticket and ends with exactly one of `ci-green` (the only success), `blocked` (a human *decision* is needed; the comment carries the question, options and a recommendation) or `failed`.

## How a package is evidenced

| tier | when | what runs |
|---|---|---|
| 0 | always, every touched skill/agent file | `lint_prose.py`: frontmatter parses, LF only, referenced files exist, contract tables agree with the scripts that implement them |
| 1 — step replay | the agent/skill of one workflow step changed **and a real past case exists** | the step runs on an input rebuilt from that case (`case_builder.py`); its verdict is compared with the recorded wrong one |
| 2 — blind test | a surface changed (a `description`, tool documentation) | a fresh model with no repo context gets only the surface and a task: does it find it, can it use it |

The tier comes from `tier_select.py` — from the kind the planner declared plus the paths — never from a model. Each scenario runs against the **old** text and the **new** text, N samples each, in isolated `claude -p` processes; the pass rule is a **delta**, not a score. The baseline is cached by a hash of the artifact, so a ticket pays only for "after". Whatever merged without executed evidence is listed in the PR under **"Not covered by tests"**.

Decidable parts of a requirement belong in a script with real tests — that is the developer plugin's job. A package that contains one ends `blocked` with "split the package".

## Agents

`prose-context-extractor` (sonnet) · `prose-planner` (opus) · `scenario-critic` (sonnet wrapper around one isolated opus process — the only critic; it judges whether scenarios are honest, never wording) · `prose-writer` (opus; never sees the scenarios' expectations) · `prose-reviewer` (sonnet).

## The scripts, by hand

All model-free except the two that start `claude -p`. Every one prints its usage in its header.

```
python scripts/lint_prose.py --root . --all
python scripts/tier_select.py --requirements requirements.json [--worktree . --base origin/main]
python scripts/scenario_validate.py scenarios/*.json
python scripts/case_builder.py --ticket-file ticket.json --at 2026-09-20T18:48:00Z --step triage --out case.md
python scripts/blind_run.py --scenario scenarios/S1.json --root . --git-ref origin/main --label baseline --out-dir evidence
python scripts/blind_run.py --scenario scenarios/S1.json --root . --label after --out-dir evidence
python scripts/evidence_merge.py --scenarios-dir scenarios --results-dir evidence --out evidence-merged.json
python scripts/scenario_critic_run.py --spec spec.md --plan plan.md --scenarios-dir scenarios --out-dir critic
```

`case_builder.py` takes the ticket as the JSON the project-issues MCP's `get_ticket` returns. It archives nothing: comments, session transcripts (`~/.claude/projects/`) and git already are the history. It prints its limits (`LIMIT:` — ticket bodies are not versioned) and exits 4, writing nothing, when the transcript folder is not on this machine.

## Develop

```
pip install -e ".[test]"
python -m pytest -q
```

`tests/` exercises the scripts as real child processes (a stand-in `claude` records how it was started) and the hooks under Node. The skill and the agents are prose and carry no phrase tests — only tier 0. See `AGENTS.md` for the decisions behind all of this.

## Release

Actions → **release** → `version=X.Y.Z`. See `AGENTS.md`, "Release".
