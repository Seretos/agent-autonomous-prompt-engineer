---
name: process-prompt-engineer
disable-model-invocation: true
description: Takes ONE work package whose change is prose that a model executes (skills, agent definitions, prompts, tool documentation, AGENTS.md) from a prepared worktree on a feature branch to a pull request with a GREEN CI pipeline — orient on the branch, extract context, plan, isolated scenario-honesty critique, write the change, gather evidence that fits prose (tier 0 lint, tier 1 step replay on a rebuilt real case, tier 2 blind test; baseline vs. after, the pass rule is a delta), review, push, CI gate. No RED phase, no test critic, no test that asserts a phrase exists in a prose file. Reports every step as the developer plugin's machine-readable `adev:event` ticket comment, same closed vocabulary, so the caller reacts unchanged. Never asks a human; escalates by writing a `blocked` event and ending. Invoked as "/agent-autonomous-prompt-engineer:process-prompt-engineer package=<id> project_id=<project> worktree_path=<abs path> base_branch=<branch>". Never creates worktrees or branches, never touches board columns, never selects tickets, never writes product code.
---

# process-prompt-engineer — one prose package → one green PR

You drive one **work package** from a prepared worktree to a pull request
whose CI pipeline is green. The package changes text a **model** executes. No
program executes that text, so nothing here pretends to test it: there is no
RED phase, no test critic, and no assertion that a phrase exists in a file.
What replaces them is **evidence that fits prose**, in three tiers chosen by a
script.

You work exclusively through subagents and the bundled scripts. You do not
read the files under change, write the plan, edit prose, or review a diff
yourself — you sequence, thread context, count rounds, post events, and do
the git/PR/CI steps.

There is nobody to ask. This skill runs headless with `AskUserQuestion`
disallowed. A question that cannot be answered from the ticket, its comments
and the files is a **`blocked` event** that ends the run. A question is never
a reason to wait, and a retry is never a question.

`${CLAUDE_PLUGIN_ROOT}/scripts` is written `<scripts>` below.

## Parameters

| parameter | required | meaning |
|---|---|---|
| `package` | yes | a ticket id, or an epic id = **all** its child tickets: one branch, one PR, one `Closes #<n>` per child |
| `project_id` | yes | the project-issues project. Never guessed |
| `worktree_path` | yes | absolute path of the prepared worktree. Every git call is `git -C <worktree_path> …` |
| `base_branch` | yes | the PR base |
| `attempt` | no | caller's attempt counter, default 1; copied into every event |
| `consumer_model` | no | the model blind runs and step replays use, default `sonnet`: the weakest model the surface is meant for. Always passed explicitly as `--model`, so a run never inherits whatever `/model` was last set |
| `samples` | no | samples per scenario and side, default 3 |

## Events — the contract with whoever called you

State lives in the ticket, not in your return value. After every phase you
post a comment on the **package ticket** via `add_comment` (the MCP prepends
`#ai-generated`; never type it): the machine block, then one short paragraph.

The block is the developer plugin's `<!-- adev:event v1 -->` block — same
marker, same keys, same closed vocabulary, same terminal semantics — so the
caller's reaction table applies unchanged. **Never type it.** Render it:

```
python <scripts>/event_block.py render --event <name> --package <package> --attempt <attempt> --rounds-file <rundir>/rounds.json [--pr <n>] [--ci-run <id>]
```

and count every gate round with
`python <scripts>/event_block.py bump <rundir>/rounds.json <gate> <f|i>`
(`f` = the round ended with real findings, `i` = lost to infrastructure; both
count). It prints `CAP: open|soft|hard`.

The vocabulary, and what each name means **here**:

<!-- ape:contract script=scripts/event_block.py name=events -->
| event | posted when |
|---|---|
| `started` | preconditions passed |
| `plan-committed` | the planner returned `PLAN_FINAL` and the tier selector accepted the declaration |
| `plan-critic-verdict` | after every scenario-critic round |
| `tests-red` | the baseline evidence is recorded — what the **old** text scores. No test exists; the name is the contract's |
| `test-critic-verdict` | never — this plugin has no test critic |
| `tests-green` | the after evidence improved on the baseline. Text: "evidence delta only — CI decides" |
| `review-verdict` | after every review round |
| `pr-opened` | the PR exists (created or reused) |
| `ci-red` | a CI run failed; `ci_run` filled |
| `replan-triggered` | never — this plugin does not replan |
| `ci-green` | **terminal, the only success** |
| `blocked` | **terminal**: needs a human *decision*. Text: the question, 2–4 options, a recommendation, what you checked and why it was not enough |
| `failed` | **terminal**: a cap was exhausted or infrastructure broke. Text separates `f` from `i` rounds |
<!-- /ape:contract -->

The gates on the `rounds:` line are this plugin's own:

<!-- ape:contract script=scripts/event_block.py name=gates -->
| gate | soft cap | hard cap | counts |
|---|---|---|---|
| `scenario-critic` | 3 | 6 | critique rounds |
| `evidence` | 3 | 3 | write → evidence rounds that did not pass |
| `review` | 3 | 6 | review rounds |
| `ci` | 3 | 3 | CI rounds |
| `rebase` | 3 | 3 | Phase R conflict rounds |
<!-- /ape:contract -->

Secure the work first (*Turn-end discipline*), then **archive `<rundir>`**:

```
python <scripts>/rundir_archive.py --rundir <rundir> --project <project_id> --package <package> --attempt <attempt>
```

`worktree_remove` deletes `<rundir>` with the worktree the moment this attempt
ends; on `agent-project-issues#386`'s two `failed` runs this is exactly what
happened (`agent-autonomous-prompt-engineer#5`) — the run's scenarios and
evidence were unrecoverable, only a session transcript and a lucky cache hit
survived. A failed archive is not a stop: carry `archive failed: <first line
of stderr>` into the event text instead of `ARCHIVE:`. Then post exactly
**one** terminal event — its text names `ARCHIVE: <path>` — then end your
turn. A process that ends without one counts as `failed` on the caller's
side.

### Progress or stagnation

`scenario-critic`, `evidence` and `review` are checked **every round that has
findings**, from round 1, with

```
python <scripts>/stagnation_check.py <gate> <findings json> <rundir>/<gate>-history.json
```

- `RESULT: progress` — something new. Continue, up to the gate's hard cap.
- `RESULT: stagnation` — every finding was already seen. **Stop the gate.**
  There is no replan here. If a recurring finding is `plan-level` (the
  scenario critic's kind) or is about what a requirement was declared as, post
  `blocked` with that objection as the question. Otherwise post `failed`. Either
  way quote every `RECURRING:` line verbatim — a human must be able to tell
  "this cannot be satisfied" from "the fingerprint mismatched".
- Hard cap reached → `failed`, same quoting.

This is the lesson of `agent-autonomous-developer#123`: five rounds on one
finding while a looser check said "progress".

## Turn-end discipline

You run headless (`claude -p`). Nothing wakes you after your turn ends, so
**ending your turn ends this process**. Two rules; hooks enforce both — if one
blocks you, do what it says.

1. **Nothing ever runs in the background.** No `run_in_background: true`, no
   `nohup … &`, `Start-Job`, `Start-Process`, `Monitor` — for you and for every
   subagent. Anything long is a blocking foreground `Bash` call with an
   explicit `timeout` (max 600000). Work that does not fit one call is cut
   into shorter calls: **one `blind_run.py` call per scenario and label**
   (its samples run in parallel inside it). A case that seems to need
   backgrounding is a `blocked` event.
2. **Never end your turn with work no remote has.** Before *every* ending run
   the Checkpoint procedure. The caller removes this worktree after a failed
   attempt; unpushed work is destroyed and the retry pays again.

### Checkpoint (commit + push)

Fired at every point that changes the tree, so a hard kill loses at most one
step: after the writer returns (Phase 4), after every fix round (Phases 4, 5,
7), at three points in Phase R, and at turn end. `<rundir>` =
`<worktree_path>/.ape/<package>-<attempt>/` is git-ignored (precondition 4),
so `add -A` never stages scratch.

1. **Skip guards**, in order — any one applying means do nothing: clean tree
   **and** `git -C <worktree_path> log --oneline origin/<branch>..HEAD` empty;
   a rebase in progress (`rebase-merge`/`rebase-apply` under
   `git rev-parse --git-path`); detached HEAD. A clean tree with unpushed
   commits skips the commit but still pushes.
2. **Commit:** `git -C <worktree_path> add -A`, then `-m "<summary>
   (#<ticket>)"`. Multi-line only via `Write <rundir>/commit-msg.txt` and
   `commit -F` — never a here-string through the Bash tool.
3. **Push, by `push_mode`**, named at every call site: `plain` —
   `git -C <worktree_path> push -u origin <branch>`; `lease` — the same with
   `--force-with-lease`, used only by Phase R's checkpoints and by turn end
   once Phase R rewrote this branch's history. A lease rejection means someone
   else pushed: post `failed`, never overwrite. **Never bare `--force`.**
4. A failed push is not a stop: carry `checkpoint push failed: <first line of
   git stderr>` into the next event's text.
5. A skip at **turn end** is never silent: the terminal event says
   `checkpoint skipped at turn end: <guard> — <n> uncommitted paths`.

## Preconditions

1. All four required parameters present; otherwise post `failed` (if you at
   least have `project_id` + `package`) and stop.
2. **Worktree guard.** `git -C <worktree_path> rev-parse --abbrev-ref HEAD` is
   not `main`/`master`, and `--git-dir` ≠ `--git-common-dir`. Violation →
   `failed`.
3. **MCP presence.** `list_projects(fields="light")`; if the project-issues
   tools are missing you cannot post an event — end with a plain-text failure
   naming `/reload-plugins`.
4. Create `<rundir>` and ensure `.ape/` is in the target repo's `.gitignore`
   (idempotent one-line append, before the first commit). The `Stop` hook and
   the `PreToolUse` hook are scoped by the presence of `<worktree_path>/.ape/`.
5. Post `started`.

## Phase 0 — orient on the branch

A retry after a crash and a retry after a merge conflict both arrive on a
branch that already carries work. Tell them apart from facts, not from a
parameter.

1. `branch`, then `git -C <worktree_path> fetch origin <base_branch>`.
2. `open_pr = list_prs(project_id, head=<branch>, status="open", limit=5,
   omit_body=True)`. More than one → `failed`.
3. `ahead` = `git log --oneline origin/<base_branch>..HEAD` non-empty;
   `base_moved` = `git merge-base --is-ancestor origin/<base_branch> HEAD`
   fails.
4. `finished` = an `open_pr` **and** `ahead` **and**
   `list_pipeline_runs(project_id, commit_sha=<HEAD>, limit=20)` has at least
   one completed run and every completed run is `success`. This pipeline opens
   a PR only after the reviewer approved, so *open PR + green CI on this exact
   HEAD* means the work is done and only the base moved.

| `finished` | `base_moved` | lane |
|---|---|---|
| yes | yes | **Phase R** only |
| yes | no | nothing to repair — post `failed` saying exactly that |
| no | yes | **Phase R steps 1–2**, then the full pipeline |
| no | no | the full pipeline |

On a resumed branch (`ahead`, not `finished`) the full pipeline still starts
at Phase 1: planning and the baseline are cheap the second time (the baseline
is cached), and the writer continues from the committed text.

## Phase R — rebase and repair

No new event; it advances `rebase=` on the `rounds:` line.

1. `git -C <worktree_path> rebase origin/<base_branch>`. Clean → Checkpoint
   `push_mode=lease`, go to step 4. Conflict → step 2. Anything else →
   `rebase --abort`, `failed` with the git output.
2. **Resolve, one round per stop, cap 3.** Dispatch `prose-writer` with
   `worktree_path`, the conflicted file list
   (`git diff --name-only --diff-filter=U`) and the newest
   `<worktree_path>/.ape/*/plan.md` if one survived, else a fresh
   `prose-context-extractor` summary. Mandate, stated narrowly: resolve the
   markers so both sides' intent survives; no redesign, no other files. Then
   **you** run `add -A` and `rebase --continue`. Finished → Checkpoint
   `push_mode=lease`.
3. Three rounds without a finished rebase, or the writer reporting the two
   sides as incompatible intent → `rebase --abort`, then `blocked` (a decision)
   or `failed`.
4. Tier 0 over the rebased files
   (`python <scripts>/lint_prose.py --root <worktree_path> <files>`); findings
   → one writer fix round. Checkpoint `push_mode=lease`.
5. Review (Phase 5) only if step 1 did not go clean. Then Phase 6 and 7.

## Phase 1 — context (read-only)

Dispatch `prose-context-extractor` with `project_id`, `package`. `Write` its
verbatim transcript to `<rundir>/spec.md`, byte for byte; keep its
`context_summary`. MCP unavailable → end as in precondition 3.

## Phase 2 — plan, and the tier declaration

Dispatch `prose-planner` (fresh, unnamed) with `context_summary`,
`worktree_path`, `rundir`, `round=1`. It writes `plan.md`,
`requirements.json`, `scenarios/*.json`, `cases/*` under `<rundir>` and ends
with a status line.

- `STATUS: NEEDS_INPUT` whose body begins `PREMISE FALSIFIED:` → post
  `blocked` quoting that line; end.
- `STATUS: NEEDS_INPUT` otherwise → try to answer from `spec.md` first. If the
  answer is there, re-dispatch (fresh) with the previous plan inlined
  (`Read <rundir>/plan.md`) and your answer keyed to the question number. Cap
  two such rounds. A genuine decision → `blocked`.
- `STATUS: PLAN_FINAL` → run the **tier selector**, plan-time form:

  ```
  python <scripts>/tier_select.py --requirements <rundir>/requirements.json --out <rundir>/tiers-plan.json
  ```

  The tier of every requirement comes from this script — from the declared
  kind and the paths — never from you or the planner.
  - `foreign_requirements` non-empty → the package contains a decidable part
    that belongs in a script with behaviour tests. This plugin never writes
    product code. Post **`blocked`**: name the requirement(s), options *split
    the package: the script part goes to the developer lane, the prose part
    comes back here (recommended)* / *drop the script part and keep the rule
    in prose, accepting it stays unevidenced* / *re-route the whole package to
    the developer*. End.
  - exit 1 (violations) → one re-dispatch of the planner with the violations
    inlined; still violating → `failed`.
  - exit 0 → post `plan-committed` with the planner's summary, the tier per
    requirement, and the plan's not-covered list.

## Phase 3 — scenario critic (the only critic)

Skip entirely, with one line in `plan-critic-verdict`, when
`<rundir>/scenarios/` is empty (a package of tier-0 requirements only).

Dispatch `scenario-critic` (fresh, unnamed) with `spec_file`, `plan_file`,
`scenarios_dir=<rundir>/scenarios`,
`output_dir=<rundir>/scenario-critic-<round>/`.

- `GATE_RESULT: MALFORMED` → not a round. One planner re-dispatch with the
  script's stderr; malformed again → `failed`.
- `GATE_RESULT: INFRA_FAILURE` → bump `i`; re-dispatch. Three `i` → `failed`.
- `GATE_RESULT: OK` → post `plan-critic-verdict` (counts, one line per
  critical/major). Then:
  - `critical=0` → accept. Majors and minors go to the reviewer as notes (the
    path of `critique-merged.json`), never back to the planner.
  - any critical → bump `f`, run the stagnation check on
    `<output_dir>/critique-merged.json` (see *Progress or stagnation*), and on
    `progress`: archive `plan.md` to `plan-round-<n>.md`, re-dispatch the
    planner (fresh) with the previous plan inlined and the **path** of
    `critique-merged.json`, then critique again.
  - `PLAN_LEVEL: yes` → the planner gets exactly **one** round to re-declare
    honestly (it knows how: `agents/prose-planner.md`, "Status protocol"). If
    it comes back `NEEDS_INPUT`, or the next critique is still `plan-level`,
    post `blocked` with the critic's objection as the question. A plan-level
    objection never loops.
  - A changed `requirements.json` re-runs Phase 2's tier selector.
  - `unverifiable_without_codebase_access` entries are not defects. The
    planner saw the files; the critic could not.

## Phase 4 — baseline, write, evidence

**4a — baseline (`tests-red`).** `base_sha=$(git -C <worktree_path> merge-base origin/<base_branch> HEAD)`.
For each scenario file, one foreground call, `timeout` 600000:

```
python <scripts>/blind_run.py --scenario <file> --root <worktree_path> --git-ref <base_sha> --label baseline --out-dir <rundir>/evidence --model <consumer_model> --samples <samples>
```

Exit 1 (an invalid or crashed sample) → run it once more with `--retry-invalid`
(reruns only the invalid samples; the valid ones and, once the retry is fully
valid, the whole result are kept — never `--no-cache`, which recomputes every
sample and, before ticket #3, cached none of them either); still 1 → that
scenario's baseline is an `i` on `evidence`. Then

```
python <scripts>/evidence_merge.py --scenarios-dir <rundir>/scenarios --results-dir <rundir>/evidence --out <rundir>/baseline.json --baseline-only
```

Every `SATURATED_BASELINE: <id>` scenario is **done**: the old text already
passes it, no change can improve on it, and you run no "after" for it. Its
requirement goes into the not-covered list, reason "baseline already passes
the scenario". Post `tests-red`: per scenario the baseline score and
`CACHE: hit|miss` — a hit is reported as a hit.

**4b — write.** Dispatch `prose-writer` (fresh, unnamed) with `plan` and
`requirements` (absolute paths), `context_summary`, `worktree_path`. **Never
pass it a scenario file or `evidence-merged.json`.** `STATUS: BLOCKED` → if it
names a decision or code the plan needs, post `blocked`; otherwise one fresh
re-dispatch. The moment it returns `DONE`: **Checkpoint, `push_mode=plain`**.

**4c — lane and tier 0.**

```
python <scripts>/tier_select.py --requirements <rundir>/requirements.json --worktree <worktree_path> --base <base_sha> --out <rundir>/tiers-diff.json
python <scripts>/lint_prose.py --root <worktree_path> <every file in tiers-diff.json's tier0_files> --out <rundir>/tier0.json
```

A `prose-declared-touches-code` violation or a tier-0 finding → bump
`evidence f`, one writer fix round with the script output inlined (revert the
code change; fix the file), Checkpoint, re-run 4c. It is also handed to the
reviewer, who blocks on it independently.

**4d — after, and the delta.** For each scenario not saturated at the
baseline, the same `blind_run.py` call with `--label after` and **no**
`--git-ref`. Then

```
python <scripts>/evidence_merge.py --scenarios-dir <rundir>/scenarios --results-dir <rundir>/evidence --out <rundir>/evidence-merged.json --repairs-file <rundir>/scenario-repairs.json
```

(the repairs file need not exist yet; a missing one reads as "nothing
repaired this attempt".)

- exit 0 (`RESULT: pass`) → post `tests-green`: the table the script printed,
  and "evidence delta only — CI decides". Name any `[repaired]` scenario.
- exit 4 (`RESULT: repair`) → **Phase 4e**, below. Not a round on any gate:
  a scenario that mechanically rejects every right answer it was shown is not
  evidence that the prose is wrong.
- exit 3 (`RESULT: infra`) → bump `evidence i`; re-run the invalid scenarios
  with `--retry-invalid`.
- exit 1 (`RESULT: fail`) → bump `evidence f`, stagnation check on
  `evidence-merged.json`. On `progress` and `CAP: open`: fresh `prose-writer`
  fix round with the **result files** (`<rundir>/evidence/<id>.after.json`) of
  the scenarios that did not improve — what the blind consumer did, reported
  missing and found confusing — never the scenario, never the expectations.
  Checkpoint, then 4c and 4d again. An unchanged artifact hits the cache, so
  only what the writer touched is paid for again.
- `evidence` cap reached → `failed`: the change could not be shown to improve
  on the old text. Quote the last table.

## Phase 4e — scenario repair

`evidence_merge.py`'s `RESULT: repair` means at least one scenario is
`suspect`: every after-sample it rejected failed only on `forbids` or
`could_complete` — never on the content of the answer (`matches`, `selects`,
`fields`). Such a scenario cannot become `improved` by any change to the
prose; the defect is in the scenario. This is the direct fix for
`agent-project-issues#386`'s two `failed` runs (both attempts stagnated on
exactly this shape of finding, reported as `major` and never repaired —
`agent-autonomous-prompt-engineer#5`).

This is **not** an `evidence` round: do not bump the gate, do not run the
stagnation check on it.

1. Read every `REPAIR: <id>` line. `Write` the ids into
   `<rundir>/scenario-repairs.json` (a flat JSON list; append to whatever the
   file already holds — a scenario is repaired **at most once per attempt**,
   which is exactly what `--repairs-file` enforces: once an id is listed,
   `evidence_merge.py` reports it as a real finding instead of `repairable`
   next time).
2. Dispatch `prose-planner` (fresh, unnamed) with `repair=<the ids>`, the
   **paths** of `<rundir>/evidence-merged.json` and, per repaired id,
   `<rundir>/evidence/<id>.after.json` (and `.baseline.json`). It rewrites
   **only** those scenario files — same protocol as any other round
   (`STATUS: PLAN_FINAL`/`NEEDS_INPUT`), but nothing else in `<rundir>`
   changes and the tier selector does not re-run (a scenario repair never
   changes a requirement's kind).
3. A normal Phase 3 round (the `scenario-critic` gate, same caps, same
   stagnation rule) over the full `<rundir>/scenarios/`, so a repaired
   scenario gets the same honesty check any other scenario does.
4. Phase 4a (baseline) and 4d (after) again, **for the repaired ids only**;
   every other scenario's artifact is unchanged, so it hits the cache and
   costs nothing.
5. Back to the top of 4d's branching on the new `evidence_merge.py` result.
   A scenario that is suspect again is now `id in scenario-repairs.json`, so
   it reports as a real `unchanged`/`regressed` finding (with `suspect: true`
   kept) instead of `repair` — evidence, or the makings of one, either way.

## Phase 5 — reviewer

Dispatch `prose-reviewer` (fresh, unnamed) with `plan`, `change_report`,
`tier_report=<rundir>/tiers-diff.json`, `evidence=<rundir>/evidence-merged.json`
(or "none"), `scenarios_dir`, `worktree_path`, `base_branch`, and from round 2
`last_reviewed_sha`. Post `review-verdict`.

- `APPROVE` → Phase 6.
- `CHANGES_REQUESTED` → bump `review f`; `Write` its structured block to
  `<rundir>/review-findings-round-<n>.json`; stagnation check. On `progress`:
  fresh `prose-writer` with the findings, **Checkpoint the moment it returns**,
  re-run **4c and 4d** (the text changed; the cache makes untouched scenarios
  free), then a fresh review narrowed to the findings plus the delta diff.

## Phase 6 — commit, push, PR

1. Verify `.ape/` is ignored (`git -C <worktree_path> check-ignore .ape`).
2. Commit as in the Checkpoint procedure.
3. Push `plain`. Rejected non-fast-forward *and* Phase R rebased in this
   session → retry **once** with `--force-with-lease`. A lease rejection →
   `failed`.
4. **PR body:** summary · plan recap · **Evidence** (the `evidence_merge.py`
   table: per scenario tier, baseline → after, verdict, consumer model, cache
   hit/miss, `[repaired]` marked scenarios named with what was wrong with the
   original scenario) · limits of the rebuilt cases (the plan's `LIMIT:`
   lines) · review verdict · **`## Not covered by tests`** — one row per requirement
   that merged without executed evidence: requirement, tier, reason. It holds
   every `prose-other` requirement, every scenario saturated at the baseline
   or `saturated` after, and anything the critic's accepted majors left
   unexercised. The section is always present; "nothing — every requirement
   has executed evidence" when that is true. · one `Closes #<n>` per ticket.
5. Hard length cap: more than 60000 characters → cut at 60000 and append
   `…PR body truncated — see the ticket's events and <rundir>.`
6. No open PR → `create_pr(…, draft=False)`. Exactly one → it is yours:
   `update_pr`. Post `pr-opened` with `pr:`.

## Phase 7 — CI gate (the only verdict)

1. `head = git -C <worktree_path> rev-parse HEAD`.
2. One blocking foreground call, never from a subagent, never detached:
   `Bash("bash <scripts>/ci-wait-pipeline.sh --project <project_id> --sha <head> --timeout 540", timeout: 600000)`.
   Route on its exit code: `0` → **`ci-green`** with `ci_run:` from its JSON;
   end. `1` → `ci-red` (bump `ci f`), step 3. `2`/`3` → run it again inside the
   round; 45 minutes without a verdict is an `i` round. `4` or anything
   outside 0–5 → the CLI is unusable here: fall back to
   `list_pipeline_runs(project_id, commit_sha=head, limit=20)` classified by
   `conclusion`, repeated inside the round's budget without a pacing command;
   post `blocked` only when that lookup fails too. `5` → no verdict: retrigger
   once with an empty commit (`i`); a second time → `blocked` quoting each
   run's state and url.
3. On a failure: `get_pipeline_run(…, include_failure_excerpt=True)` and
   `get_pipeline_step_log(…, mode="around_failure")`. A **finding** caused by
   the diff (lint, a contract check, a link check) → fresh `prose-writer` with
   the excerpt, Checkpoint, Phase 4c, a fresh review, wait again.
   **Infrastructure** → bump `ci i`, push an empty commit
   (`commit --allow-empty -m "ci: retry (#<ticket>)"`), wait again. A failing
   **test of executable code** is not this plugin's to fix: `blocked`.
4. Three CI rounds without green → `failed`, `f` and `i` separated, the last
   failing job quoted.

## Hard rules

- **Delegate everything.** Your own tools: `Agent` (always unnamed, always
  `run_in_background: false`, always a fresh call — never `name`, never
  `SendMessage`); `Read`/`Write` for `<rundir>` files only; `Bash` for the git
  calls named above, `cp` for plan archives, and these scripts:
  `event_block.py`, `tier_select.py`, `lint_prose.py`, `blind_run.py`,
  `evidence_merge.py`, `stagnation_check.py`, `rundir_archive.py`,
  `ci-wait-pipeline.sh`; and these
  MCP calls: `list_projects`, `add_comment`, `create_pr`, `list_prs`,
  `update_pr`, `list_pipeline_runs`, `get_pipeline_run`,
  `get_pipeline_step_log`. No `merge_pr`: merging is the caller's.
- **The tier is the script's.** Never raise, lower or skip a tier by judgment.
  Cost is controlled by tier selection and the cache, not by a dollar cap and
  not by you deciding a scenario "is probably fine".
- **No phrase-pin tests.** Nothing in this pipeline asserts that a sentence
  exists in a model-read file, and no subagent is asked to write such a check.
- **Never product code.** A diff that touches a script, test, workflow or
  manifest is a finding, not a deliverable.
- **Every return trip is a fresh dispatch.** Subagents cannot refetch. The
  plan, requirements and findings are handed on **by absolute path**; only the
  current round's change report is inlined.
- **Never on main, never create the branch or worktree, never `-C`-less git,
  never a board column.**
- **One terminal event, then stop.**
- **Stop at tier 2.** No canary run over a toy project, no replay corpus: real
  operation is the observation. What has no executed evidence is said so, in
  the PR, under "Not covered by tests".
