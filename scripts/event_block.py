#!/usr/bin/env python3
"""
Renders the `<!-- adev:event v1 -->` machine block and keeps the round counters.

The block is the contract with the caller (agent-ticket-orchestrator's `run`
parses it as dumb `key: value` lines). It is the developer plugin's contract,
copied, not extended: same marker, same keys, the same CLOSED event vocabulary,
the same terminal semantics. This plugin uses a subset of the names and adds
none. Rendering it from a script instead of from the skill's prose is the
"decidable part goes into a script" rule applied to this plugin itself: a
misspelt event name or a malformed `rounds:` line is a caller-side parse bug,
and nothing about it needs a model.

Usage:
  event_block.py render --event <name> --package <id> [--attempt <n>]
                        --rounds-file <json> [--pr <n>] [--ci-run <id>]
      prints the block (the caller appends one human paragraph and posts it).
  event_block.py bump <rounds-file> <gate> <f|i|c>
      counts one round against a gate (f = ended with findings, i = lost to
      infrastructure, c = ran and passed cleanly). Creates the file on first
      use. Prints `USED: <n>/<soft>` and `CAP: open|soft|hard`, both computed
      from f + i only — c counts on the `rounds:` line, never toward the cap.
  event_block.py --print-contract <events|terminal-events|gates>
      the tables as JSON, read by lint_prose.py's contract check.

Exit 2 on any usage or vocabulary error — never a silently wrong block.
"""
import argparse
import json
import os
import sys

# A piped stdout on Windows defaults to the ANSI code page; every consumer of
# these scripts (the skill, the tests, CI) reads UTF-8.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

# The developer plugin's closed vocabulary, verbatim and in its order.
EVENTS = [
    "started", "plan-committed", "plan-critic-verdict", "tests-red",
    "test-critic-verdict", "tests-green", "review-verdict", "pr-opened",
    "ci-red", "replan-triggered", "ci-green", "blocked", "failed",
]
TERMINAL_EVENTS = ["ci-green", "blocked", "failed"]

# gate -> (soft cap, hard cap). The soft cap is what the rounds line shows,
# exactly like the developer's `/3`. Order is the order on the line.
GATES = {
    "scenario-critic": (3, 6),
    "evidence": (3, 3),
    "review": (3, 6),
    "ci": (3, 3),
    "rebase": (3, 3),
}

CONTRACTS = {
    "events": EVENTS,
    "terminal-events": TERMINAL_EVENTS,
    "gates": list(GATES),
}


def load_rounds(path):
    rounds = {gate: {"f": 0, "i": 0, "c": 0} for gate in GATES}
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            stored = json.load(fh)
        for gate, counts in stored.items():
            if gate not in GATES:
                raise ValueError(f"unknown gate {gate!r} in {path}")
            rounds[gate] = {
                "f": int(counts.get("f", 0)),
                "i": int(counts.get("i", 0)),
                "c": int(counts.get("c", 0)),
            }
    return rounds


def rounds_line(rounds):
    parts = []
    for gate, (soft, _hard) in GATES.items():
        f, i, c = rounds[gate]["f"], rounds[gate]["i"], rounds[gate]["c"]
        # used counts every round, clean ones included; the cap (elsewhere)
        # is decided by f + i alone, so the parenthesised suffix stays f/i.
        parts.append(f"{gate}={f + i + c}/{soft}({f}f,{i}i)")
    return " ".join(parts)


def render(event, package, attempt, rounds, pr="", ci_run=""):
    if event not in EVENTS:
        raise ValueError(f"unknown event {event!r}; the vocabulary is closed: {', '.join(EVENTS)}")
    return "\n".join([
        "<!-- adev:event v1",
        f"event: {event}",
        f"package: {package}",
        f"attempt: {attempt}",
        # This plugin never replans; the key stays because the caller's
        # contract carries it.
        "generation: 1/2",
        f"rounds: {rounds_line(rounds)}",
        f"pr: {pr}",
        f"ci_run: {ci_run}",
        "-->",
    ])


def cap_state(gate, used):
    soft, hard = GATES[gate]
    if used >= hard:
        return "hard"
    if used >= soft:
        return "soft"
    return "open"


def main(argv):
    if len(argv) == 3 and argv[1] == "--print-contract":
        if argv[2] not in CONTRACTS:
            sys.stderr.write(f"unknown contract {argv[2]!r}; expected one of {sorted(CONTRACTS)}\n")
            return 2
        print(json.dumps(CONTRACTS[argv[2]]))
        return 0

    parser = argparse.ArgumentParser(prog="event_block.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--event", required=True)
    r.add_argument("--package", required=True)
    r.add_argument("--attempt", default="1")
    r.add_argument("--rounds-file", required=True)
    r.add_argument("--pr", default="")
    r.add_argument("--ci-run", default="")
    b = sub.add_parser("bump")
    b.add_argument("rounds_file")
    b.add_argument("gate")
    b.add_argument("kind", choices=["f", "i", "c"])
    args = parser.parse_args(argv[1:])

    try:
        if args.cmd == "render":
            print(render(args.event, args.package, args.attempt,
                         load_rounds(args.rounds_file), args.pr, args.ci_run))
            return 0
        if args.gate not in GATES:
            raise ValueError(f"unknown gate {args.gate!r}; expected one of {list(GATES)}")
        rounds = load_rounds(args.rounds_file)
        rounds[args.gate][args.kind] += 1
        with open(args.rounds_file, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(rounds, fh, indent=2)
            fh.write("\n")
        used = rounds[args.gate]["f"] + rounds[args.gate]["i"]
        print(f"USED: {used}/{GATES[args.gate][0]}")
        print(f"CAP: {cap_state(args.gate, used)}")
        return 0
    except (ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
