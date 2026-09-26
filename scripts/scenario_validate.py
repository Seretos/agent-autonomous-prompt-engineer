#!/usr/bin/env python3
"""
Validates scenario files and catches the mechanically decidable half of
scenario dishonesty before any model is paid for. Also holds `score_sample`,
the one place a sample is scored against a scenario — `evidence_merge.py`
imports it, so a sample is judged the same way whether it is a control
checked here or a real blind-consumer answer merged there.

A scenario is one JSON file the planner writes per tier-1/tier-2 requirement:

  {
    "id": "S1",
    "requirement": "R1",
    "tier": 2,
    "surface": {"kind": "skill", "paths": ["skills/foo/SKILL.md"]},
    "task": "what the blind consumer is asked to do",
    "expect": {"selects": "foo", "matches": ["<regex>"], "forbids": ["<regex>"],
               "fields": {"<name>": <value>}},
    "fields": {"<name>": {"type": "boolean", "question": "…"}
                        | {"enum": ["a", "b"], "question": "…"}},
    "controls": [
      {"outcome": "pass", "answer_text": "a real right answer, in its own words"},
      {"outcome": "fail", "answer_text": "a plausible wrong answer"}
    ]
  }

  tier 1 (step replay) instead carries
    "surface": {"kind": "agent-step", "paths": ["agents/triage.md"]},
    "case_input": "<path to a case_builder.py output, relative to the scenario file>",
    "recorded_wrong": "<regex matching the verdict the step gave in the real incident>"
  `recorded_wrong` is an implicit `forbids`; `expect` adds what a right verdict shows.

`fields` (tier 2, `skill`/`agent`/`doc` only — `agent-step` has no JSON schema, so a
scenario that carries both is malformed) asks the consumer a yes/no or multiple-choice
question directly, instead of hunting the fact out of free text with a negation regex:
`blind_run.py` adds it to the consumer's JSON schema, verbatim; `expect.fields` compares
the answer exactly. Use it for a fact a `forbids` regex would otherwise have to phrase as
a negation ("assigning is not enough") — that phrasing is exactly what a Markdown `**not**`
defeats (see `negation-trap` below).

`controls` are two or more real samples — a `pass` control (a right answer, ideally in
its own words) and a `fail` control (a plausible wrong one) — checked against `expect`
with the same `score_sample` a real blind run is scored with, before any model is paid
for. They are the mechanical half of "would a wrong change still pass" and "would a right
answer still fail", the two questions the scenario-critic otherwise has to answer by eye.

Surface kinds:
  skill | agent   staged as a throw-away plugin and exposed with --plugin-dir,
                  so discovery runs through the harness's real mechanism
  doc             tool documentation or a prompt: handed to the consumer as text
  agent-step      tier 1 only: the file's body becomes the system prompt of the
                  replayed step

Findings (exit 1):
  malformed       shape/regex errors, missing/malformed `controls`, `fields` on an
                  `agent-step` scenario
  answer-leak     an `expect.matches` regex already matches the task text or a field's
                  question, or the task names the surface it is supposed to discover —
                  the consumer would pass by echoing the question
  echo-trap       an `expect.forbids` regex already matches the task text or a field's
                  question — a consumer that states the (correct) answer in the task's
                  own words is rejected for repeating the question, seen on
                  agent-autonomous-prompt-engineer#5 (agent-project-issues#386, attempt 2)
  unfalsifiable   nothing in `expect` could fail
  control-mismatch a `pass` control fails `score_sample`, or a `fail` control passes it
  negation-trap   a `pass` control still scores true when every negation in its answer
                  (`not`, `never`, `n't`, …) is wrapped in Markdown emphasis
                  (`**…**`/`*…*`/`_…_`) — the incident this exists for:
                  agent-autonomous-prompt-engineer#5, a forbid's negative lookbehind
                  could not see "not" through `**not**`

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
FINDING_CODES = ["malformed", "answer-leak", "echo-trap", "unfalsifiable",
                  "control-mismatch", "negation-trap"]
CONTRACTS = {"surface-kinds": SURFACE_KINDS, "finding-codes": FINDING_CODES}

FLAGS = re.IGNORECASE | re.DOTALL
NEGATION = re.compile(r"\b(?:not|no|never|cannot|without)\b|\w+n't\b", re.IGNORECASE)
EMPHASIS_STYLES = [("**", "**"), ("*", "*"), ("_", "_")]


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


def field_questions(scenario):
    fields = scenario.get("fields") or {}
    return [str(spec.get("question") or "") for spec in fields.values() if isinstance(spec, dict)]


def leak_surface(scenario):
    """Everything the consumer sees before answering: the task plus every
    field's question. A `matches`/`forbids` regex that already matches this
    text leaks or traps on the question alone, never on an answer."""
    return "\n".join([scenario.get("task") or ""] + field_questions(scenario))


def score_sample(scenario, sample):
    """List of reasons the sample fails `scenario.expect`; empty means it
    passes. Each reason is `{"check": "selects|matches|fields|forbids|
    could_complete", "what": <str>}` — the `check` is what
    `evidence_merge.py` uses to tell a scenario-shaped failure (forbids,
    could_complete) from a content-shaped one (matches, selects, fields)."""
    reasons = []
    expect = scenario.get("expect") or {}
    text = sample.get("answer_text") or ""

    selects = expect.get("selects")
    if selects:
        chosen = [str(s) for s in sample.get("selected") or []]
        if not any(c == selects or c.endswith(":" + selects) for c in chosen):
            reasons.append({"check": "selects",
                            "what": f"did not select {selects!r} (selected: {chosen or 'nothing'})"})

    for pattern in expect.get("matches") or []:
        if not re.search(pattern, text, FLAGS):
            reasons.append({"check": "matches", "what": f"answer does not match {pattern!r}"})

    fields = expect.get("fields") or {}
    if fields:
        structured = sample.get("structured")
        got_fields = (structured.get("fields") if isinstance(structured, dict) else None) or {}
        for name, want in fields.items():
            got = got_fields.get(name, "<missing>")
            if got != want:
                reasons.append({"check": "fields",
                                "what": f"field {name!r} = {got!r}, expected {want!r}"})

    for pattern in forbids_of(scenario):
        if re.search(pattern, text, FLAGS):
            reasons.append({"check": "forbids", "what": f"answer matches forbidden {pattern!r}"})

    structured = sample.get("structured")
    if isinstance(structured, dict) and structured.get("could_complete") is False:
        reasons.append({"check": "could_complete", "what": "consumer reported could_complete: false"})
    return reasons


def negation_variants(text):
    """(label, variant) pairs — `text` with every negation wrapped in one
    Markdown emphasis style. Only styles that actually changed the text are
    returned (a control with no negation yields none)."""
    variants = []
    for open_, close_ in EMPHASIS_STYLES:
        variant = NEGATION.sub(lambda m: f"{open_}{m.group(0)}{close_}", text)
        if variant != text:
            variants.append((f"{open_}…{close_}", variant))
    return variants


def validate_controls(scenario, add):
    controls = scenario.get("controls")
    if not isinstance(controls, list):
        add("malformed", "`controls` must be a list")
        return
    passes = [c for c in controls if isinstance(c, dict) and c.get("outcome") == "pass"]
    fails = [c for c in controls if isinstance(c, dict) and c.get("outcome") == "fail"]
    if not passes or not fails:
        add("malformed", "`controls` needs at least one `outcome: \"pass\"` and one "
                          "`outcome: \"fail\"` sample")
        return
    for control in controls:
        if not isinstance(control, dict) or control.get("outcome") not in ("pass", "fail"):
            add("malformed", f"control {control!r} needs `outcome`: \"pass\" or \"fail\"")
            continue
        sample = {"answer_text": control.get("answer_text") or "",
                   "selected": control.get("selected") or [],
                   "structured": control.get("structured")}
        try:
            reasons = score_sample(scenario, sample)
        except (re.error, TypeError) as exc:
            add("malformed", f"control for {scenario.get('id', '?')} could not be scored: {exc}")
            continue
        if control["outcome"] == "pass" and reasons:
            add("control-mismatch",
                f"positive control fails: {reasons[0]['what']}")
        elif control["outcome"] == "fail" and not reasons:
            add("control-mismatch", "negative control passes every check in `expect`")
        if control["outcome"] == "pass":
            for label, variant in negation_variants(sample["answer_text"]):
                tripped = score_sample(scenario, dict(sample, answer_text=variant))
                if any(r["check"] == "forbids" for r in tripped):
                    add("negation-trap",
                        f"emphasising every negation ({label}) in the positive control "
                        "trips a `forbids` regex that should not care about Markdown")
                    break


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

    fields = scenario.get("fields")
    if fields is not None:
        if kind == "agent-step":
            add("malformed", "`fields` needs a JSON-schema consumer; `agent-step` replays "
                              "a step's free-text output, so it cannot carry `fields`")
        elif not isinstance(fields, dict) or not fields:
            add("malformed", "`fields` must be a non-empty object of `<name>: {type|enum, question}`")
        else:
            for name, spec in fields.items():
                if not isinstance(spec, dict) or not (spec.get("question") or "").strip():
                    add("malformed", f"field {name!r} needs a non-empty `question`")
                elif spec.get("type") not in ("boolean",) and not spec.get("enum"):
                    add("malformed", f"field {name!r} needs `type: \"boolean\"` or an `enum` list")
        for name in (expect.get("fields") or {}):
            if not isinstance(fields, dict) or name not in fields:
                add("malformed", f"`expect.fields` names {name!r}, which `fields` does not declare")

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
        if not (expect.get("matches") or expect.get("forbids") or expect.get("selects")
                or expect.get("fields")):
            add("unfalsifiable", "`expect` is empty: no sample could ever fail")
        surface_text = leak_surface(scenario)
        for pattern in expect.get("matches") or []:
            try:
                if re.search(pattern, surface_text, FLAGS):
                    add("answer-leak", f"`matches` regex {pattern!r} already matches the "
                                        "task (or a field's question)")
            except (re.error, TypeError):
                pass
        for pattern in forbids_of(scenario):
            try:
                if re.search(pattern, surface_text, FLAGS):
                    add("echo-trap", f"`forbids` regex {pattern!r} already matches the task "
                                      "(or a field's question) — a consumer that states the "
                                      "right answer in the task's own words is rejected for it")
            except (re.error, TypeError):
                pass
        selects = expect.get("selects")
        if selects and re.search(re.escape(str(selects)), task, FLAGS):
            add("answer-leak", f"the task names {selects!r}, the surface it is meant to discover")

    if tier in (1, 2):
        validate_controls(scenario, add)
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
