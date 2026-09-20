#!/usr/bin/env python3
"""
Validates scenario files and catches the mechanically decidable half of
scenario dishonesty before any model is paid for.

A scenario is one JSON file the planner writes per tier-1/tier-2 requirement:

  {
    "id": "S1",
    "requirement": "R1",
    "tier": 2,
    "surface": {"kind": "skill", "paths": ["skills/foo/SKILL.md"]},
    "task": "what the blind consumer is asked to do",
    "expect": {"selects": "foo", "matches": ["<regex>"], "forbids": ["<regex>"]}
  }

  tier 1 (step replay) instead carries
    "surface": {"kind": "agent-step", "paths": ["agents/triage.md"]},
    "case_input": "<path to a case_builder.py output, relative to the scenario file>",
    "recorded_wrong": "<regex matching the verdict the step gave in the real incident>"
  `recorded_wrong` is an implicit `forbids`; `expect` adds what a right verdict shows.

Surface kinds:
  skill | agent   staged as a throw-away plugin and exposed with --plugin-dir,
                  so discovery runs through the harness's real mechanism
  doc             tool documentation or a prompt: handed to the consumer as text
  agent-step      tier 1 only: the file's body becomes the system prompt of the
                  replayed step

Findings (exit 1):
  malformed       shape/regex errors
  answer-leak     an `expect.matches` regex already matches the task text, or
                  the task names the surface it is supposed to discover — the
                  consumer would pass by echoing the question
  unfalsifiable   nothing in `expect` could fail

Whether a scenario is tailored to the solution is NOT decidable here; that is
the scenario-critic's job, in an isolated process.

Usage:
  scenario_validate.py <scenario.json> [<scenario.json> ...]
  scenario_validate.py --print-contract <surface-kinds|finding-codes>
"""
import json
import os
import re
import sys

# A piped stdout on Windows defaults to the ANSI code page; every consumer of
# these scripts (the skill, the tests, CI) reads UTF-8.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

SURFACE_KINDS = ["skill", "agent", "doc", "agent-step"]
FINDING_CODES = ["malformed", "answer-leak", "unfalsifiable"]
CONTRACTS = {"surface-kinds": SURFACE_KINDS, "finding-codes": FINDING_CODES}


def load_scenario(path):
    with open(path, encoding="utf-8") as fh:
        scenario = json.load(fh)
    if not isinstance(scenario, dict):
        raise ValueError("a scenario is a JSON object")
    scenario["_path"] = os.path.abspath(path)
    return scenario


def case_input_path(scenario):
    value = scenario.get("case_input") or ""
    if os.path.isabs(value):
        return value
    return os.path.join(os.path.dirname(scenario["_path"]), value)


def forbids_of(scenario):
    expect = scenario.get("expect") or {}
    forbids = list(expect.get("forbids") or [])
    if scenario.get("recorded_wrong"):
        forbids.append(scenario["recorded_wrong"])
    return forbids


def validate(scenario):
    findings = []

    def add(code, what):
        findings.append({"scenario": str(scenario.get("id", "?")), "code": code, "what": what})

    for key in ("id", "requirement"):
        if not scenario.get(key):
            add("malformed", f"missing `{key}`")
    tier = scenario.get("tier")
    surface = scenario.get("surface") or {}
    kind = surface.get("kind")
    if tier not in (1, 2):
        add("malformed", "`tier` must be 1 or 2 (tier 0 needs no scenario)")
    if kind not in SURFACE_KINDS:
        add("malformed", f"`surface.kind` must be one of {SURFACE_KINDS}")
    if not surface.get("paths"):
        add("malformed", "`surface.paths` must name the file(s) under test")

    expect = scenario.get("expect") or {}
    patterns = list(expect.get("matches") or []) + forbids_of(scenario)
    for pattern in patterns:
        try:
            re.compile(pattern)
        except (re.error, TypeError) as exc:
            add("malformed", f"regex {pattern!r} does not compile: {exc}")

    if tier == 1:
        if kind != "agent-step":
            add("malformed", "a tier-1 scenario replays a step: `surface.kind` must be `agent-step`")
        if not scenario.get("recorded_wrong"):
            add("malformed", "a tier-1 scenario needs `recorded_wrong` — the verdict the real case got")
        if not scenario.get("case_input"):
            add("malformed", "a tier-1 scenario needs `case_input` (case_builder.py output)")
        elif not os.path.isfile(case_input_path(scenario)):
            add("malformed", f"case_input {scenario['case_input']!r} does not exist")
    if tier == 2:
        if kind == "agent-step":
            add("malformed", "`agent-step` is tier 1 only")
        task = scenario.get("task") or ""
        if not task.strip():
            add("malformed", "a tier-2 scenario needs a `task`")
        if not (expect.get("matches") or expect.get("forbids") or expect.get("selects")):
            add("unfalsifiable", "`expect` is empty: no sample could ever fail")
        for pattern in expect.get("matches") or []:
            try:
                if re.search(pattern, task, re.IGNORECASE | re.DOTALL):
                    add("answer-leak", f"`matches` regex {pattern!r} already matches the task text")
            except (re.error, TypeError):
                pass
        selects = expect.get("selects")
        if selects and re.search(re.escape(str(selects)), task, re.IGNORECASE):
            add("answer-leak", f"the task names {selects!r}, the surface it is meant to discover")
    return findings


def main(argv):
    if len(argv) == 3 and argv[1] == "--print-contract":
        if argv[2] not in CONTRACTS:
            sys.stderr.write(f"unknown contract {argv[2]!r}\n")
            return 2
        print(json.dumps(CONTRACTS[argv[2]]))
        return 0
    if len(argv) < 2:
        sys.stderr.write("usage: scenario_validate.py <scenario.json> ...\n")
        return 2

    findings = []
    for path in argv[1:]:
        try:
            findings.extend(validate(load_scenario(path)))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            findings.append({"scenario": path, "code": "malformed", "what": str(exc)})
    for f in findings:
        print(f"{f['scenario']}: [{f['code']}] {f['what']}")
    print(f"SCENARIOS: {'FAIL' if findings else 'OK'} ({len(argv) - 1} file(s), {len(findings)} finding(s))")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
