/**
 * hooks/check-no-background.mjs
 *
 * PreToolUse hook (matcher `Bash|Monitor`): the mechanical form of the
 * "nothing ever runs in the background" rule.
 *
 * Copied as a contract from agent-autonomous-developer (its tickets #23, #93,
 * #101 and agent-worktree#176 are the incidents behind it; its AGENTS.md,
 * "Nothing runs in the background", is the full account). The short form: a
 * headless `claude -p` session is never woken after its turn ends, so a
 * backgrounded command — or a `Monitor` armed to wait for one — dies with the
 * process, and its diagnostics die with it. A backgrounded command is wrong
 * the moment it is issued, so this hook refuses it up front.
 *
 * ## What it refuses
 *
 *   - `Bash` with `run_in_background: true`
 *   - `Bash` whose command detaches on its own: `nohup …`, `Start-Job`,
 *     `Start-Process`, or a trailing `&` (lib/turn-end-scan.mjs,
 *     backgroundReasonForBash — the same classifier the Stop hook uses, so
 *     what this hook refuses and what that one detects cannot drift apart)
 *   - every `Monitor` call
 *
 * ## Scope gate
 *
 * A PreToolUse hook fires in every session that loads this plugin, including
 * a human's interactive one, where `Monitor` and background commands are
 * legitimate. The hook activates only when
 *   (a) `<cwd>/.ape/` exists — a live `process-prompt-engineer` run (the
 *       skill creates it in its preconditions and the caller starts the
 *       session with cwd = the worktree), or
 *   (b) the payload's `agent_type` names one of this plugin's subagents.
 *
 * ## How it refuses
 *
 * Exit code 2 with the reason on stderr. The reason carries
 * NO_BACKGROUND_MARKER so the Stop hook can see in the transcript that the
 * call was refused and nothing is running.
 *
 * All failure modes (bad stdin, unknown tool, no scope match) pass — fail-safe.
 */

import { existsSync } from "node:fs";
import path from "node:path";
import process from "node:process";

import {
  NO_BACKGROUND_MARKER,
  agentNameOf,
  backgroundReasonForBash,
} from "./lib/turn-end-scan.mjs";

/** Subagents of this plugin for which the rule holds unconditionally. */
const PLUGIN_AGENTS = new Set([
  "prose-context-extractor",
  "prose-planner",
  "prose-writer",
  "prose-reviewer",
  "scenario-critic",
]);

const RULE =
  "Hard rule: nothing is ever started with run_in_background: true, " +
  "`nohup … &`, Start-Job, Start-Process or Monitor. Everything runs " +
  "synchronously in the foreground, inside this turn, as a Bash call with an " +
  "explicit `timeout` (max 600000 ms); work that does not fit one call is cut " +
  "into shorter calls (one blind_run.py call per scenario and label), never " +
  "detached. There is no case in which backgrounding is right — if you " +
  "believe you found one, that is a `blocked` event, not a background task.";

function refuse(what) {
  process.stderr.write(
    `${NO_BACKGROUND_MARKER} refused ${what}. ${RULE}`,
  );
  process.exit(2);
}

async function main() {
  let payload;
  try {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    process.exit(0);
  }

  // --- Scope gate ---
  const cwd = String(payload.cwd ?? "");
  const inPipelineRun = Boolean(cwd) && existsSync(path.join(cwd, ".ape"));
  const isPluginAgent = PLUGIN_AGENTS.has(agentNameOf(payload.agent_type));
  if (!inPipelineRun && !isPluginAgent) process.exit(0);

  // --- Decision ---
  const tool = String(payload.tool_name ?? "");
  if (tool === "Monitor") {
    refuse("Monitor");
  }
  if (tool === "Bash") {
    const reason = backgroundReasonForBash(payload.tool_input);
    if (reason !== null) {
      const command = String(payload.tool_input?.command ?? "(unknown command)");
      refuse(`Bash(${reason}): ${command}`);
    }
  }

  process.exit(0);
}

main().catch(() => {
  process.exit(0);
});
