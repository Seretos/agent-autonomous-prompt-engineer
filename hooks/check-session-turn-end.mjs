/**
 * hooks/check-session-turn-end.mjs
 *
 * Stop hook: blocks the top-level `process-prompt-engineer` session from
 * ending its turn while work is still outstanding.
 *
 * Copied as a contract from agent-autonomous-developer (its tickets #22, #23,
 * #101; full account in its AGENTS.md). Headless (`claude -p`) there is no
 * loop that wakes the session after its turn ends, so ENDING THE TURN ENDS
 * THE PROCESS. `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS` only sets how long the
 * process loiters before it is killed (measured there at 600 s, 0 and 2 h —
 * all three died the same way); it never turns waiting into resuming.
 *
 * ## What it checks
 *
 *   A. Unresolved backgrounded command — a `Monitor` does not resolve it; only
 *      the PreToolUse refusal (hooks/check-no-background.mjs) does.
 *   B. Unpreserved work — uncommitted changes, or commits no remote has. The
 *      caller removes the worktree after a failed attempt, so anything not
 *      pushed is destroyed and the retry pays for context, planning and
 *      evidence a second time.
 *
 * ## Scope gate
 *
 * A Stop hook fires on every turn end of every session that loads this plugin,
 * including a human's. The gate is the presence of `<cwd>/.ape/`:
 * `process-prompt-engineer` creates `<worktree_path>/.ape/<package>-<attempt>/`
 * in its preconditions, and the caller starts the session with cwd = the
 * worktree. No `.ape/` directory, no pipeline run, no hook. Keep this gate and
 * the skill's precondition 4 in sync.
 *
 * `stop_hook_active` caps this at a single block per turn.
 *
 * All failure modes (bad stdin, unreadable transcript, git unavailable, no
 * `.ape/`) are treated as "do not block" — fail-safe.
 *
 * Block output: write JSON {"decision":"block","reason":"..."} to stdout, exit 0.
 * Pass: exit 0 with no stdout.
 */

import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import process from "node:process";

import {
  block,
  readTranscriptLines,
  unresolvedBackgroundCommand,
} from "./lib/turn-end-scan.mjs";

/**
 * Run a git command in `cwd` and return trimmed stdout, or null on any
 * failure (git missing, not a repository, non-zero exit, timeout).
 */
function git(cwd, args) {
  try {
    return execFileSync("git", ["-C", cwd, ...args], {
      encoding: "utf8",
      timeout: 10_000,
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return null;
  }
}

async function main() {
  // --- 1. Read and parse stdin as the hook payload ---
  let payload;
  try {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    process.exit(0);
  }

  // --- 2. Never block twice on the same turn ---
  if (payload.stop_hook_active === true) process.exit(0);

  // --- 3. Scope gate: only inside a live process-prompt-engineer run ---
  const cwd = String(payload.cwd ?? "");
  if (!cwd || !existsSync(path.join(cwd, ".ape"))) process.exit(0);

  // --- 4. Condition A: a backgrounded command nothing waited on ---
  const lines = readTranscriptLines(payload.transcript_path);
  const unresolved = unresolvedBackgroundCommand(lines);
  if (unresolved) {
    block(
      "process-prompt-engineer: the turn is ending with a backgrounded command still " +
        `unresolved (${unresolved}). ` +
        "This session is headless (claude -p): there is no loop that wakes it " +
        "after the turn ends, so ENDING THE TURN ENDS THE PROCESS and that " +
        "command is killed with it — no wait-ceiling setting changes that " +
        "(measured at 600s, at 0, and at 2h; all three died). A Monitor does " +
        "not help either — nothing wakes a headless process. Backgrounding " +
        "was never allowed. " +
        "Continue this turn and wait for that command with a blocking " +
        "foreground Bash call (poll its log or pid in an in-command loop, " +
        "explicit `timeout`), or kill it and re-run the work as synchronous " +
        "foreground chunks. Do not end the turn expecting to be resumed.",
    );
  }

  // --- 5. Condition B: work that would not survive the turn ---
  const dirty = git(cwd, ["status", "--porcelain"]);
  if (dirty === null) process.exit(0); // not a git checkout / git unavailable
  const unpushed = git(cwd, ["rev-list", "--count", "HEAD", "--not", "--remotes"]);
  const unpushedCount = Number.parseInt(unpushed ?? "0", 10) || 0;

  if (dirty !== "" || unpushedCount > 0) {
    const branch = git(cwd, ["rev-parse", "--abbrev-ref", "HEAD"]) ?? "(unknown)";
    const changed = dirty === "" ? 0 : dirty.split(/\r?\n/).length;
    block(
      "process-prompt-engineer: the turn is ending with work that no remote has " +
        `(branch ${branch}: ${changed} changed path(s), ${unpushedCount} ` +
        "unpushed commit(s)). The caller removes this worktree after a failed " +
        "attempt, so anything not pushed is destroyed and the retry pays for " +
        "context, planning and evidence a second time. Commit everything on " +
        "the feature branch and `git -C <worktree_path> push -u origin " +
        "<branch>` BEFORE ending the turn — this holds for every ending, " +
        "including `blocked` and `failed`, not just the happy path. If the " +
        "state is genuinely not worth keeping, commit it anyway: a discarded " +
        "commit costs nothing, a lost implementation costs the whole attempt.",
    );
  }

  process.exit(0);
}

main().catch(() => {
  // Any unexpected error — fail-safe, do not block.
  process.exit(0);
});
