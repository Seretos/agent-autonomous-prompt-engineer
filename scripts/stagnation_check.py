#!/usr/bin/env python3
"""
Tells a gate round that found something NEW apart from one that only repeats
what an earlier round already found. Contract copied from
agent-autonomous-developer's stagnation-check.py (not imported), reduced to
this plugin's gates.

Why it exists here: agent-autonomous-developer#123 ran five test-critic rounds
on the same finding while a looser check said "progress". A round that repeats
is not a reason for another round — and in this plugin it is not a reason for
a replan either (there is none): the skill turns stagnation into a terminal
event, `blocked` when the repeating objection is plan-level, `failed` otherwise.

Mechanical on purpose: a fixed, quoted fingerprint compared for exact equality.
A model here could wave a stagnating round through as progress on its opinion.

Fingerprints, by gate:
  scenario-critic, evidence   (kind, violated_criterion), from findings at
                              severity critical or major
  review                      (kind, file, what[:80]), from findings at
                              severity blocking

The history file is a flat JSON list of [kind, key] pairs. This script reads
it, decides, and appends this round's new fingerprints; it never resets it.

Usage:
  stagnation_check.py <gate> <findings-json> <history-json>
  stagnation_check.py --print-contract gates
Prints `RESULT: progress` or `RESULT: stagnation`; a round with no fingerprint
at all prints `RESULT: clean`. Exit 0 on a verdict, 2 on a usage/input error —
never a default verdict on bad input.
"""
import json
import sys

# A piped stdout on Windows defaults to the ANSI code page; every consumer of
# these scripts (the skill, the tests, CI) reads UTF-8.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

GATE_RULES = {
    "scenario-critic": {"severities": {"critical", "major"}, "keyed_on": "violated_criterion"},
    "evidence": {"severities": {"critical", "major"}, "keyed_on": "violated_criterion"},
    "review": {"severities": {"blocking"}, "keyed_on": "file+what"},
}


def fingerprint(gate, finding):
    kind = finding.get("kind", "")
    if GATE_RULES[gate]["keyed_on"] == "violated_criterion":
        key = finding.get("violated_criterion", "")
    else:
        key = f"{finding.get('file', '')}::{(finding.get('what') or '')[:80]}"
    return [kind, key]


def main(argv):
    if len(argv) == 3 and argv[1] == "--print-contract":
        if argv[2] != "gates":
            sys.stderr.write(f"unknown contract {argv[2]!r}\n")
            return 2
        print(json.dumps(list(GATE_RULES)))
        return 0
    if len(argv) != 4:
        sys.stderr.write("usage: stagnation_check.py <gate> <findings-json> <history-json>\n")
        return 2
    gate, findings_path, history_path = argv[1:4]
    if gate not in GATE_RULES:
        sys.stderr.write(f"unknown gate {gate!r}; expected one of {sorted(GATE_RULES)}\n")
        return 2

    try:
        with open(findings_path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"could not read findings from {findings_path}: {exc}\n")
        return 2
    findings = payload.get("findings") if isinstance(payload, dict) else None
    if not isinstance(findings, list):
        sys.stderr.write(f"{findings_path}: 'findings' is not a list\n")
        return 2

    try:
        with open(history_path, encoding="utf-8") as fh:
            history = json.load(fh)
    except FileNotFoundError:
        history = []
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"{history_path}: {exc}\n")
        return 2
    if not isinstance(history, list):
        sys.stderr.write(f"{history_path}: expected a JSON list\n")
        return 2

    severities = GATE_RULES[gate]["severities"]
    current = [fingerprint(gate, f) for f in findings if f.get("severity") in severities]
    seen = {tuple(fp) for fp in history}
    new = []
    for fp in current:
        if tuple(fp) not in seen:
            seen.add(tuple(fp))
            new.append(fp)

    with open(history_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(history + new, fh, indent=2)
        fh.write("\n")

    if not current:
        print("RESULT: clean")
    else:
        print(f"RESULT: {'progress' if new else 'stagnation'}")
        for fp in current:
            if fp not in new:
                print(f"RECURRING: {fp[0]} | {fp[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
