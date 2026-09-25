#!/usr/bin/env python3
"""
Runs the scenario-honesty critique in ONE isolated `claude -p` process and
writes the findings the skill routes on.

This plugin has exactly one critic-like role. It does not judge the plan's
wording (no test for prose exists to defend) — only whether the scenarios are
honest: not tailored to the solution, no leak of the answer into the task or
the surface, falsifiable, on the requirement.

ISOLATION is the developer plugin's critic contract, copied: no settings
sources, no MCP, no skills, no tools, a fixed system prompt, a JSON schema,
cwd an empty directory outside every repository. The package is assembled HERE,
from verbatim files — the curator of a review package is the one role that
could defang a critique, so no model assembles it.

Before any model is paid for, scenario_validate.py's mechanical findings are
computed. A `malformed` scenario stops the run (exit 2: fix the file, this is
not a critique round). `answer-leak` / `unfalsifiable` found mechanically are
merged into the result as critical findings next to the critic's own.

Usage:
  scenario_critic_run.py --spec <spec.md> --plan <plan.md> --scenarios-dir <dir>
                         --out-dir <dir> [--model opus] [--effort high] [--timeout 480]

Writes into <out-dir>: package.txt, critique.raw.json, critique.json,
provenance.txt, stderr.txt and critique-merged.json. Prints GATE_RESULT: OK or
GATE_RESULT: INFRA_FAILURE. Exit 0 on a complete critique (whatever it found),
1 on an infrastructure failure, 2 on a usage error or a malformed scenario.
"""
import argparse
import datetime
import glob
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ape_common import (ISOLATION_FLAGS, canonical, fresh_workdir, run_isolated,  # noqa: E402
                        sha256_file, write_json)
from blind_run import consumer_contract  # noqa: E402
from scenario_validate import case_input_path, load_scenario, validate  # noqa: E402

# A piped stdout on Windows defaults to the ANSI code page; every consumer of
# these scripts (the skill, the tests, CI) reads UTF-8.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
SYSTEM_PROMPT = os.path.join(HERE, "critic", "scenario-critic-system-prompt.txt")
SCHEMA = os.path.join(HERE, "critic", "scenario-critic-schema.json")
CASE_INPUT_CHARS = 8000
SEVERITIES = ["critical", "major", "minor"]


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def assemble_package(spec, plan, scenarios):
    parts = ["PART 1 — SPECIFICATION (the ticket, verbatim)\n\n" + spec,
             "PART 2 — PLAN (verbatim)\n\n" + plan]
    blocks = []
    for scenario in scenarios:
        public = {k: v for k, v in scenario.items() if not k.startswith("_")}
        block = f"--- scenario {scenario['id']} ---\n" + json.dumps(public, indent=2, ensure_ascii=False)
        if scenario.get("case_input"):
            case = read(case_input_path(scenario))
            cut = " (cut)" if len(case) > CASE_INPUT_CHARS else ""
            block += (f"\n\n--- case input of {scenario['id']}, first {CASE_INPUT_CHARS} "
                      f"characters{cut} ---\n" + case[:CASE_INPUT_CHARS])
        blocks.append(block)
    parts.append("PART 3 — SCENARIOS (verbatim)\n\n" + "\n\n".join(blocks))
    # From the same constants blind_run.py actually starts the consumer with
    # — never restated by hand, so a task that assumes a tool or live data
    # the consumer does not have is checked against the real contract.
    parts.append("PART 4 — " + consumer_contract())
    return ("\n\n" + "=" * 78 + "\n\n").join(parts) + "\n"


def main(argv):
    parser = argparse.ArgumentParser(prog="scenario_critic_run.py")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--scenarios-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--model", default="opus")
    parser.add_argument("--effort", default="high")
    parser.add_argument("--timeout", type=int, default=480)
    args = parser.parse_args(argv[1:])

    try:
        spec, plan = read(args.spec), read(args.plan)
        paths = sorted(glob.glob(os.path.join(args.scenarios_dir, "*.json")))
        if not paths:
            raise ValueError(f"no scenario files in {args.scenarios_dir}")
        scenarios = [load_scenario(p) for p in paths]
        system_prompt, schema = read(SYSTEM_PROMPT), read(SCHEMA)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    mechanical = [f for s in scenarios for f in validate(s)]
    malformed = [f for f in mechanical if f["code"] == "malformed"]
    if malformed:
        for f in malformed:
            sys.stderr.write(f"{f['scenario']}: [malformed] {f['what']}\n")
        sys.stderr.write("malformed scenario(s): fix the file(s); no critique was run\n")
        return 2

    os.makedirs(args.out_dir, exist_ok=True)
    out = lambda name: os.path.join(args.out_dir, name)  # noqa: E731
    package = assemble_package(spec, plan, scenarios)
    with open(out("package.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(package)

    workdir = fresh_workdir("ape-critic-")
    try:
        argv_used, rc, stdout, stderr = run_isolated(
            ["--model", args.model, "--effort", args.effort,
             "--disable-slash-commands", "--tools", "",
             "--system-prompt", system_prompt,
             "--output-format", "json", "--json-schema", schema],
            package, workdir, args.timeout)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    with open(out("critique.raw.json"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(stdout)
    with open(out("stderr.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(stderr)
    with open(out("provenance.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join([
            "scenario-critic isolated run",
            f"timestamp_utc: {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
            f"cwd: {workdir} (fresh, empty, removed after the run)",
            f"package_sha256: {sha256_file(out('package.txt'))}",
            f"system_prompt_sha256: {sha256_file(SYSTEM_PROMPT)}",
            f"isolation_flags: {ISOLATION_FLAGS} --disable-slash-commands --tools '' "
            "--system-prompt <file> --json-schema <file>",
            f"model: {args.model}", f"effort: {args.effort}", f"exit_code: {rc}", ""]))

    critique, error = None, None
    if rc != 0:
        error = f"critic process exited {rc}"
    else:
        try:
            envelope = json.loads(stdout)
            if envelope.get("is_error"):
                error = f"CLI reported an error result: {envelope.get('subtype')}"
            else:
                critique = envelope.get("structured_output")
                if not isinstance(critique, dict) or not isinstance(critique.get("findings"), list):
                    critique, error = None, "no structured_output with a findings list"
        except json.JSONDecodeError as exc:
            error = f"unparseable CLI envelope: {exc}"
    if error:
        print("GATE_RESULT: INFRA_FAILURE")
        print(f"REASON: {error}")
        print(f"STDERR: {canonical(out('stderr.txt'))}")
        return 1
    write_json(out("critique.json"), critique)

    findings = []
    for f in mechanical:
        findings.append({
            "id": f"mechanical::{f['scenario']}::{f['code']}", "scenario": f["scenario"],
            "kind": f["code"], "severity": "critical", "violated_criterion": f["scenario"],
            "what": f["what"], "source": "scenario_validate.py"})
    for index, f in enumerate(critique["findings"]):
        record = dict(f)
        record["id"] = f"critic::{f.get('id', index + 1)}"
        record["source"] = "critic"
        findings.append(record)

    counts = {s: sum(1 for f in findings if f.get("severity") == s) for s in SEVERITIES}
    plan_level = [f for f in findings if f.get("kind") == "plan-level"]
    merged = {
        "severity_counts": counts,
        "plan_level": bool(plan_level),
        "findings": findings,
        "solid": critique.get("solid") or [],
        "unverifiable_without_codebase_access":
            critique.get("unverifiable_without_codebase_access") or [],
    }
    write_json(out("critique-merged.json"), merged)

    print("GATE_RESULT: OK")
    print(f"SEVERITY: critical={counts['critical']} major={counts['major']} minor={counts['minor']}")
    print(f"PLAN_LEVEL: {'yes' if plan_level else 'no'}")
    for f in findings:
        if f.get("severity") in ("critical", "major"):
            print(f"- id: {f['id']} | scenario: {f.get('scenario')} | kind: {f.get('kind')} | "
                  f"severity: {f['severity']} | violated_criterion: {f.get('violated_criterion')} | "
                  f"what: {(f.get('what') or '')[:200]}")
    print(f"MERGED: {canonical(out('critique-merged.json'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
