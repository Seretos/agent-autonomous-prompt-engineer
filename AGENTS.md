# agent-autonomous-prompt-engineer

Pure skill + agents plugin (no binary, no MCP). Takes **one work package whose change is prose that a model executes** — `skills/**`, `agents/**`, `AGENTS.md`, a prompt, tool documentation — from a prepared worktree to a pull request with a **green CI pipeline**, on top of the `agent-project-issues` MCP. It is the lower-layer sibling of `agent-autonomous-developer`, driven by the same upper layer (`agent-ticket-orchestrator`).

README.md covers *what* it does and how to run the scripts by hand. The skill and the agents document their own rules. This file records only the decisions a contributor must not silently break — the cross-file invariants and the rationale no single file shows.

## Why this role exists at all

The developer plugin turns a package into a PR test-first. That presupposes a program executes the requirement. When the requirement lives in a file a **model** executes, no assertion executes it; every assertion is a string comparison. The developer's gates then demand opposite things — the test-critic calls the string check a `tautology`, the plan-critic calls its absence `untestable` — and the run ends `blocked`. Seen on `agent-autonomous-developer#122` (blocked, Question), `#123` (five test-critic rounds on one finding while the stagnation check said "progress"), `agent-ticket-orchestrator#35` (triage recommended a pin test on a prose file). Patching exemptions into the developer's critics moved prose complexity into the wrong role; `agent-autonomous-developer#126` took them out again. Prose gets its own evidence here instead.

Routing — which package comes here — is **not** this plugin's: `agent-ticket-orchestrator#38`.

## Scope: one prose package in, one green PR out — and never product code

One skill, `process-prompt-engineer`, not model-invocable, started headless exactly like the developer's entry (`package`, `project_id`, `worktree_path`, `base_branch`). It knows nothing about boards, ticket selection, worktree lifecycle. And it **never writes a script, test, workflow or manifest**. A decidable part of a requirement belongs in a script with behaviour tests — that is the developer's lane, so a package carrying a `script` requirement ends `blocked` with "split the package" as the question. `tier_select.py` reports a prose-declared requirement whose diff touches code as a violation, and the reviewer blocks on it independently: a code change that arrives here has had no test-first treatment from anyone.

## The event contract is the developer's, copied — not extended, not imported

Same `<!-- adev:event v1 -->` marker, same keys, the same **closed** 13-name vocabulary, the same terminal semantics (`ci-green` the only success, `blocked` = a decision, `failed` = infra/findings), so the caller's reaction table applies unchanged. **No new event name.** `tests-red` means "baseline evidence recorded", `tests-green` means "after improved on baseline", `plan-critic-verdict` carries the scenario critic; `test-critic-verdict` and `replan-triggered` are never posted; `generation:` is always `1/2`. `rounds:` carries this plugin's own gates in the same `<gate>=<used>/<cap>(<f>f,<i>i)` form — the caller reads it as opaque prose. `<used>` counts every round a gate ran, a clean one (`c`) included, matching the developer lane's meaning of the field; the cap (`CAP: open|soft|hard`) stays decided by `f + i` alone, so a clean round never moves it.

The block is rendered by `scripts/event_block.py`, never typed by the model: a misspelt event name is a caller-side parse bug and nothing about it needs a model. The vocabulary lives in that script; `SKILL.md`'s table is checked against it by tier 0 (see "Contract tables"). If the developer's vocabulary changes, change `EVENTS` there — the contract is copied, so nothing imports it for you.

## There are no tests for prose, and no test here pretends otherwise

No RED phase, no test critic, **no test that asserts a phrase exists in a prose file** — not in the pipeline, not in this repo's own `tests/`. `tests/` holds behaviour tests for the scripts (real child processes, real files, a real git repo, the real incident as a fixture) and for the hooks. The skill and the agents carry exactly one check: `lint_prose.py --all` (tier 0), which reads only what something besides a human reads. The first real package that goes through the plugin is the integration test; a regression reproduces the same `blocked` event and Question card.

When adding a test, ask what reads the string besides a human. "Nothing" means it does not belong here.

## The tier is chosen by a script, from a declared kind plus the paths

The planner declares a **kind** per requirement (`prose-step`, `prose-surface`, `prose-other`, `script`); `tier_select.py` derives the tier. A model that picks its own evidence tier picks the cheap one.

- **Tier 0** — always, free: `lint_prose.py` over every touched skill/agent file.
- **Tier 1, step replay** — `prose-step`: the one step's agent body runs as system prompt on an input rebuilt from a **real** past case; its verdict is compared with the recorded wrong one. No real case → the planner must declare `prose-other`, never invent one.
- **Tier 2, blind test** — `prose-surface`: a fresh model with no repo context gets only the surface and a task.
- Beyond tier 2 (a canary run over a toy project, a replay corpus) is **out**: real operation is the observation (`agent-autonomous-developer#123`). What merged without executed evidence is listed in the PR under **"Not covered by tests"** — requirement, tier, reason. That section is always present.

## The pass rule is a delta, and a saturated scenario is not evidence

`evidence_merge.py` compares baseline with after over N samples (default 3), model-free — same reason as the developer's `plan-critic-merge.py`: a model between the samples and the decision is a curator. `improved` passes; `unchanged`/`regressed` are findings; **`saturated`** (old text already passes every sample) is neither — the scenario cannot show the change, so the requirement goes to "Not covered". A baseline that is already saturated skips the after-run entirely.

An invalid sample (leak, crash) is not scored. Fewer than half valid → `infra`, an `i` round, never a verdict.

## A scenario that rejects a right answer is repaired once, never argued with

Seen twice on `agent-project-issues#386` (`agent-autonomous-prompt-engineer#5`): a scenario whose every failing after-sample fails only on `forbids`/`could_complete` — never on `matches`/`selects`/`fields`, i.e. never on the actual content of the answer — cannot become `improved` by any change to the prose, because the defect is in the scenario, not the text under test. `evidence_merge.py` marks such a row `suspect`; before ticket #5 the pipeline had no way to act on that distinction and the scenario-critic's own finding about it, filed as `major`, never reached the planner (a major "goes to the reviewer as notes, never back to the planner" — right for a precision question, fatal for one that guarantees `failed`).

`--repairs-file` turns a first-time suspect scenario into `RESULT: repair` (exit 4) instead of a finding: the pipeline dispatches the planner with `repair=<ids>` and the real after-answers, over the scenario files only, then re-runs the scenario critic and the evidence gate for those ids — never bumping the `evidence` gate, because the prose did nothing wrong. Once an id is in `--repairs-file`, it never gets a second free pass: a still-suspect scenario after one repair is a real (if unusual) finding, `suspect: true` kept for the reviewer and the PR body.

## Cost is controlled by tier selection and the cache — not by a dollar cap

Same reasoning as the upper plugin's AGENTS.md: a dollar cap kills a package mid-way and the retry pays again. Instead:

- The **baseline is cached** under `APE_CACHE_DIR` (default `~/.claude/ape-cache`, outside the worktree so it survives `worktree_remove`), keyed by a hash of the artifact under test plus the scenario's input — task and `fields`' questions, i.e. what the consumer is asked, never `expect` — the model and the sample count, so tightening an expectation does not re-buy samples. A ticket pays for "after" only; after a writer fix round only the scenarios whose artifact changed are paid again. A hit is reported as `CACHE: hit` and goes into the event text.
- Only a complete, fully valid run is cached — on any call, `--no-cache` included: that flag only skips the cache *read* (bypassing a suspected-stale entry), never the write. Before ticket #3 the write was gated on `not --no-cache` too, so a run forced past a stale cache was never cached either, and a ticket that had to use it paid for the same samples on every later round. `--retry-invalid` reruns only the samples an earlier result marked invalid (crashed or leaked), keeps the valid ones, and — once the merged result is fully valid — caches it; it replaced the old advice to rerun the whole scenario with `--no-cache`.
- The consumer model is the **weakest model the surface is meant for** (default `sonnet`), passed as an explicit `--model` on every process so a run never inherits the last `/model`.

## Isolation is a property of the process, and then it is checked

Every model process this plugin starts outside the session — blind consumer, step replay, scenario critic — goes through `ape_common.run_isolated`: `--setting-sources "" --strict-mcp-config`, cwd a fresh empty directory **outside every repository** (`fresh_workdir` refuses a temp dir inside a git checkout; CLAUDE.md discovery walks parents). Measured on 2026-09-21 with `claude 2.1.278`: that flag set yields `mcp_servers: []` and only the staged plugin plus builtins; bundled skills (`deep-research`, …) remain listed and are harmless. Re-measure after a CLI upgrade: `tests/fixtures/blind-skill-transcript.jsonl` is that run's transcript, and a changed `init` event shape is what would silently blind the leak check.

Only the surface is made available, **via a staged plugin dir, never the repo**: `blind_run.py` copies the listed files into a throw-away plugin and passes `--plugin-dir`, so discovery runs through the harness's real skill listing. `selected` for a skill is read from the transcript's `Skill` tool calls, not from what the consumer says it used.

What the consumer has is not a second description anyone hand-writes: `blind_run.consumer_contract()` renders it from `TOOLS_BY_KIND` and the schema-building code, `--print-contract consumer` prints it, the planner reads it before writing a task, and `scenario_critic_run.assemble_package` embeds it verbatim as the package's fourth part. A task that assumes a tool the consumer does not have is now checked against the same source the harness actually starts it with.

A started process with `Read` can still open any absolute path. So `leak_check.py` walks each sample's own stream-json transcript afterwards and marks it **invalid** on a foreign MCP/plugin/tool or a read outside the surface. Invalid is not failed.

**Reuse of existing blind-test agents** (ticket #1, item 5): `agent-mcp-tester`'s `cluster-tester` was checked and does not fit — it is a subagent *inside* a session (it inherits the session's context, the opposite of blind), needs a live MCP and sandbox projects, and tests tool behaviour, not a text surface. What is reused is its second lens, "agent-intuitiveness — judged purely from the surface", as the consumer's report shape (`missing` = sufficiency, `confusing` = ergonomics, `selected` = discoverability).

## Exactly one critic, and it judges scenarios, not wording

`scenario-critic` wraps `scenario_critic_run.py`: one isolated process, a script-assembled verbatim package (spec, plan, scenarios, and — from `blind_run.consumer_contract()` — what the blind consumer actually has), kinds `tailored` / `answer-leak` / `unfalsifiable` / `off-requirement` / `plan-level` / `rejects-right-answer`. The mechanically decidable half (`scenario_validate.py`: the task already contains the expected answer, a `forbids` regex already matches the task or a `fields` question (`echo-trap`), the task names the surface it should discover, empty expectations, a missing or mismatched `controls` pair, a positive control that a Markdown-wrapped negation still trips (`negation-trap`)) runs first and costs nothing. `rejects-right-answer` is the critic's own half of the same question the mechanical checks answer for a *given* control: can it construct any concrete right answer — in other words, or one that correctly reports `could_complete: false` because the task needs a tool the consumer contract does not list — that this scenario would still reject. It is always `critical`: under the delta rule below, a scenario that rejects a right answer can never score `improved`, whatever the change.

A plain yes/no or choice fact belongs in `fields` (a schema property `blind_run.py` adds to the consumer's structured answer) and `expect.fields` (compared exactly), not in a `forbids` negation regex — a regex has to see "not" however the consumer happened to write it, and a Markdown `**not**` alone defeated one on `agent-autonomous-prompt-engineer#5` (`agent-project-issues#386`).

Two leaks the critic cannot see are closed structurally: the **writer never receives a scenario file or `evidence-merged.json`** (on a fix round it gets the blind run's *result files* — what the consumer did and reported missing — never the expectations), and the **reviewer** checks the diff for planted scenario text.

## Rounds stop on stagnation, and a plan-level objection never loops

`stagnation_check.py` (contract copied from the developer) runs on **every** round with findings, not only at the soft cap. There is no replan here. Stagnation or a hard cap → `failed`; a recurring or `plan-level` objection → `blocked` with the objection as the question, after the planner got exactly one round to re-declare honestly. This is the direct answer to `#123`'s five rounds.

## The case builder reads history and stores nothing

`case_builder.py` rebuilds one step's input as of a point in time from what already exists: ticket comments (`created_at`), the latest `adev:event`, the session transcript slice under `~/.claude/projects/`, the commit from git. No archive, no corpus. The ticket arrives as a **file** (the MCP's `get_ticket` response, written by the planner) — the script has no provider code, because `gh` is disabled for agents in this ecosystem and a second REST client would sidestep the MCP.

Its limits are printed, never silent: a ticket body edited after the time cannot be reconstructed (bodies are not versioned; `updated_at` is bumped by every comment, so the limit is stated whenever the file cannot prove otherwise); a comment edited later is flagged; an absent transcript folder is **exit 4 and no output file** — a partial input that looks whole is worse than none. Demonstrated on the real incident of `agent-ticket-orchestrator#35`: `tests/fixtures/incident-122/ticket.json` is the real `agent-autonomous-developer#122`, and `--at 2026-09-20T18:48:00Z --step triage` yields the `blocked` event without the triage answer that followed it.

## Contract tables: prose that states a script's table is checked against the script

A table in a skill/agent file that restates a script's vocabulary is wrapped in `<!-- ape:contract script=… name=… -->` and compared by tier 0 with `<script> --print-contract <name>`. That is the one legitimate "string test on prose": something besides a human (the script) defines the tokens. Used here for the event names and gates (`event_block.py`) and the requirement kinds (`tier_select.py`). Add a marker when you add such a table; never add a check on wording.

## Turn-end discipline and checkpoints are the developer's, copied

Headless, ending the turn ends the process; nothing runs in the background; work is committed **and pushed** at every tree-changing step so a hard kill loses at most one step (`agent-autonomous-developer#115`). Two hooks enforce it (`hooks/check-no-background.mjs`, `hooks/check-session-turn-end.mjs`), scoped by `<cwd>/.ape/` **or** one of this plugin's `agent_type`s so a human's session is untouched. **Keep in sync:** the skill's precondition 4 (creates `.ape/`), the hooks' scope gate, `PLUGIN_AGENTS` in the PreToolUse hook and the files in `agents/` (`tests/test_hooks.py` covers the last pair).

Agent names carry a `prose-` prefix (`prose-planner`, not `planner`) because both lower plugins are installed side by side and an unqualified `planner` dispatch would be ambiguous.

`.ape/` is this plugin's run directory, `.adev/` the developer's; the two never share state.

Scripts force UTF-8 on stdout/stderr: a piped stdout on Windows defaults to the ANSI code page, and the first `—` in a message crashed the reader (found by the test suite on day one).

## Every run dir outlives its worktree

`worktree_remove` deletes `<worktree_path>`, and `<rundir>` lives under it — a `failed`/`blocked` attempt loses its scenarios, evidence and round history with the worktree, unrecoverably. Both `agent-project-issues#386` attempts hit exactly this (`agent-autonomous-prompt-engineer#5`): only a session transcript and one lucky `ape-cache` entry survived, and rebuilding the two fixtures under `tests/fixtures/incident-386/` from those took hours a copied directory would have made instant. `rundir_archive.py` copies `<rundir>` to `APE_ARCHIVE_DIR` (default `~/.claude/ape-runs/`, outside every worktree, same "refuse inside a git checkout" guard as `ape_common.fresh_workdir`) before every terminal event — success included, because a `ci-green` run's evidence is also worth keeping and "only archive on failure" is a branch nobody remembers to test. The event text names the archive path.

## Release

- **Release is orphan-branch + marketplace dispatch.** `release.yml` (manual: Actions → release → `version=X.Y.Z`) stamps the version, force-pushes an orphan `release` branch holding only install-ready files and POSTs a dispatch (`category: skill`) to `seretos-agents/modular-software-factory`. `main` and `release` share no history. Clients install at the tag `agent-autonomous-prompt-engineer--vX.Y.Z`.
- **The stage must ship everything `${CLAUDE_PLUGIN_ROOT}` reaches:** `skills/`, `agents/`, `scripts/`, `hooks/`. The stage step runs `lint_prose.py --root "$STAGE" --all`, which fails the release when a referenced file is missing from the stage, a contract table disagrees with the staged script, or a staged skill/agent file has CRLF.
- **Required secret:** `ECOSYSTEM_TOKEN` — the one ecosystem-wide secret name (classic PAT, `repo` + `project` scope; ideally an org-level secret).
- **`assets/icon.png` and `description.md` are release artifacts, not just repo files:** the dispatch payload sends `raw.githubusercontent.com/${repo}/${TAG}/…` URLs for both, so they must live on the orphan `release` branch at the tagged commit.
- **`.gitattributes` ships too:** Claude Code silently ignores CRLF skill/agent files, and a Windows checkout without `eol=lf` writes CRLF into the plugin cache.
- **Dependencies** are declared in `.claude-plugin/plugin.json` (`agent-project-issues`); Claude Code installs/loads them with this plugin.
- **Release notes come from `scripts/release_notes.py` run on `main`, before the orphan checkout** — `main`'s history since the previous release tag, recovered via the `Source-Commit: <sha>` trailer on that tag's orphan commit. The current release's own orphan commit must keep stamping that trailer (`git commit -m "release: vX" -m "Source-Commit: <sha>"`); dropping it silently degrades the next release's notes from the exact trailer-based range to the date-based fallback heuristic.
