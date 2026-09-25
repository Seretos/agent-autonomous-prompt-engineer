# incident-386

Real scenario files and real blind-consumer answers from the two
`process-prompt-engineer` runs on `agent-project-issues#386` (2026-09-25), both of
which ended `failed` at the evidence gate. See `agent-autonomous-prompt-engineer#5`.

Both `.ape/` run directories were deleted with their worktrees; these files were
recovered from the orchestrator's preserved `stream.jsonl` transcripts and, for the
full sample texts, from `~/.claude/ape-cache/`. Extraction: read the relevant
`Write`/`tool_result` event, strip the `^\d+\t` line-number prefix a `Read` tool result
carries, drop absolute paths (`transcript`, `argv`) and everything not needed for
scoring. Nothing in `expect`, `task` or `answer_text` was reworded.

## `a2-S1.*` — attempt 2, requirement R1, the echo-trap

Session `e6a2c86d-11bc-46f7-ba2d-448c5f8f2794`, `stream.jsonl` line 308 (the final
`Write` of `scenarios/R1.json`, 08:04:48Z) and line 591 (`Read` of the after-result
following the writer's fix round, 08:24:05Z — the samples that were still scored
`unchanged` and drove the run to `failed`).

`expect.forbids[2]` and `[3]` already match the **task** text ("say whether that call
by itself gets the PR merged"): every consumer that states the (correct) negative
answer necessarily echoes the question's own words back. All three real answers report
`could_complete: true`, the right tool (`reviewers_add`/`requested_reviewers`), and the
right fact ("stays blocked until Bob approves") — and all three fail on forbids[2]/[3]
regardless.

## `a1-S5.*` — attempt 1, requirement R5, the negation-trap

Session `46fbb3a7-fc6e-4a5c-9501-faf3ee73e9e9`. The scenario is the final `Write` of
`scenarios/R5.json`, `stream.jsonl` line 405 (round 3, 07:18:53Z); its one forbid is
byte-identical to round 2's (line 333/336), so round 2's after-run is genuine evidence
against this exact scenario.

`a1-S5.after.json` is round 2's after-run, recovered from
`~/.claude/ape-cache/8acea49aa9dde5…json` (cache key derived from the surface file and
task, not from `expect` — a hit this script deliberately does not invalidate when
`expect` tightens). Samples 1 and 2 answer correctly with "GitLab does **not** refuse
the call…" / "GitLab does **not** refuse a merge request…"; the forbid's
`(?<!not )(?<!n't )(?<!never )` lookbehind does not see `not` through the Markdown
`**…**`, so both are scored as forbidden-match failures. Sample 3 correctly reports
`could_complete: false` (the task asks what the tools would do without a project
identifier).

Round 3's own after-run (same scenario, different sample wording, cache key
`c3751d63c256cb9107f0df19cd088d3d23759b84ecf723bcd75a3ad3596d8b5`, `stream.jsonl` line
778) is not used as a fixture: none of its three samples contain the word "refuse" at
all, and it fails the same forbid for an unrelated reason (a parenthetical mentioning
GitHub's 422 response lands inside the 60-character window after "gitlab"). It confirms
the same regex is fragile in more than one way but is not the incident the ticket
quotes.

## `a1-S1.*` — attempt 1, requirement R1, the tool-mismatch

`stream.jsonl` line 320 (`Write` of `scenarios/R1.json`, round 2) and line 776 (`Read`
of the matching after-result). The task says "Using the ticket/PR tools you have in
this session" — the tier-2 blind consumer for a `skill` surface has only
`Skill, Read, Glob, Grep` (`blind_run.TOOLS_BY_KIND["skill"]`), no MCP. All three
samples correctly report `could_complete: false` and name the missing tools; sample 3
additionally crashed (`exit 1; no result event`) and is `valid: false`.
