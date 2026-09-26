#!/usr/bin/env python3
"""
Scores blind-run samples against their scenario and compares baseline with
after. Model-free, same philosophy as the developer plugin's
plan-critic-merge.py: a model between the samples and the decision would be a
curator, able to read a failed sample charitably.

SCORING, per valid sample, by `scenario_validate.score_sample` — all of:
  * `expect.selects`   one entry of the sample's `selected` equals the name, or
                       ends with `:<name>` (a plugin-qualified skill name)
  * `expect.matches`   every regex is found in the sample's answer text
  * `expect.fields`    every named field of `structured.fields` equals the
                       expected value exactly (see `scenario_validate.py`)
  * `expect.forbids`   no regex is found; a tier-1 scenario's `recorded_wrong`
                       is an implicit forbid
  * the consumer did not report `could_complete: false`
An invalid sample (leak, crash) is not scored at all: it is not evidence.

THE PASS RULE IS A DELTA, NOT AN ABSOLUTE SCORE. rate = passes / valid samples.
  improved    after > baseline (by at least --min-delta)
  saturated   baseline and after both 1.0 — the scenario cannot show this
              change; not a failure of the change, but no evidence for it:
              it goes into the PR's "Not covered by tests" section
  unchanged   no difference (or below --min-delta)
  regressed   after < baseline
  invalid     fewer than half of a side's samples are valid evidence
An absent baseline artifact (new file) scores 0.0 by construction.

SUSPECT (agent-autonomous-prompt-engineer#5). An `unchanged`/`regressed` row
whose every failing after-sample fails ONLY on `forbids`/`could_complete` —
never on `matches`/`selects`/`fields` — never shows a wrong *answer*, only a
scenario that rejects what it got. `suspect: true` on that row does not
change its verdict; it is what makes exit 4 / `--repairs-file` below fire.

REPAIR (exit 4, `RESULT: repair`). A suspect scenario not yet named in
`--repairs-file` is pulled out of `findings` into `repairable` — the pipeline
repairs the *scenario*, not the prose, and `stagnation_check.py` never sees
it as a prose finding. Once its id IS in `--repairs-file` (the pipeline ran
one repair round on it this attempt), it goes back into `findings` as usual,
with `suspect: true` kept for the reviewer and the PR body — a suspect
scenario is repaired at most once per attempt, never argued with twice.
Every row for a repaired id carries `repaired: true`, independent of verdict.
A missing `--repairs-file` (the first round of an attempt) is an empty list,
not an error.

RESULT: pass    every scenario is improved or saturated
        repair  a new repair candidate exists (checked first — repairing a
                scenario is cheap and orthogonal to any other verdict)
        fail    any (non-repairable) scenario is unchanged or regressed
        infra   any scenario is invalid (and none failed) — an `i` round

`findings` in the output is shaped for stagnation_check.py (gate `evidence`).

BASELINE-ONLY MODE (--baseline-only) scores just the baseline side, before the
writer runs. A scenario whose baseline already passes every sample is printed
as `SATURATED_BASELINE: <id>`: no change can improve on it, so the pipeline
does not pay for an "after" run and lists the requirement as not covered.

Usage:
  evidence_merge.py --scenarios-dir <dir> --results-dir <dir> --out <json>
                    [--min-delta 0.0] [--baseline-only] [--repairs-file <json>]
  evidence_merge.py --print-contract verdicts
Exit 0 pass, 1 fail, 3 infra, 4 repair, 2 usage error.
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ape_common import write_json  # noqa: E402
from scenario_validate import load_scenario, score_sample  # noqa: E402

# A piped stdout on Windows defaults to the ANSI code page; every consumer of
# these scripts (the skill, the tests, CI) reads UTF-8.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

VERDICTS = ["improved", "saturated", "unchanged", "regressed", "invalid"]
# A row is `suspect` when every failing after-sample fails on one of these
# checks only — never on a check that judges the *content* of the answer.
SCENARIO_SHAPED_CHECKS = {"forbids", "could_complete"}


def score_side(scenario, record):
    if record.get("absent"):
        return {"rate": 0.0, "valid": 0, "passes": 0, "total": 0, "absent": True,
                "cache": record.get("cache", "n/a"), "enough": True, "failing": []}
    samples = record.get("samples") or []
    valid = [s for s in samples if s.get("valid")]
    failing, passes = [], 0
    for sample in valid:
        reasons = score_sample(scenario, sample)
        if reasons:
            failing.append({"sample": sample.get("sample"), "reasons": reasons})
        else:
            passes += 1
    return {
        "rate": (passes / len(valid)) if valid else 0.0,
        "valid": len(valid), "passes": passes, "total": len(samples), "absent": False,
        "cache": record.get("cache", "miss"),
        # More than half must be real evidence, or the side says nothing.
        "enough": bool(samples) and len(valid) * 2 > len(samples),
        "failing": failing,
    }


def verdict_of(baseline, after, min_delta):
    if not baseline["enough"] or not after["enough"]:
        return "invalid"
    delta = after["rate"] - baseline["rate"]
    if delta < 0:
        return "regressed"
    if baseline["rate"] == 1.0 and after["rate"] == 1.0:
        return "saturated"
    if delta > 0 and delta >= min_delta:
        return "improved"
    return "unchanged"


def is_suspect(after):
    """True when every failing after-sample failed only on a check that
    judges the SCENARIO (a forbid, could_complete) — never one that judges
    the answer's content (matches, selects, fields). Such a row can never
    become `improved`: no right answer could pass it."""
    if not after["failing"]:
        return False
    return all(r["check"] in SCENARIO_SHAPED_CHECKS
               for f in after["failing"] for r in f["reasons"])


def merge(scenarios, records, min_delta=0.0, repairs=None):
    repairs = set(repairs or [])
    rows, findings, repairable = [], [], []
    for scenario in scenarios:
        sid = scenario["id"]
        repaired = sid in repairs
        baseline_rec, after_rec = records.get((sid, "baseline")), records.get((sid, "after"))
        if baseline_rec is not None and after_rec is None:
            baseline = score_side(scenario, baseline_rec)
            if baseline["enough"] and not baseline["absent"] and baseline["rate"] == 1.0:
                # Saturated at the baseline: the pipeline deliberately ran no "after".
                rows.append({"id": sid, "requirement": scenario.get("requirement"),
                             "tier": scenario.get("tier"), "model": baseline_rec.get("model"),
                             "baseline": baseline, "after": baseline, "delta": 0.0,
                             "verdict": "saturated", "repaired": repaired})
                continue
        if baseline_rec is None or after_rec is None:
            missing = "baseline" if baseline_rec is None else "after"
            rows.append({"id": sid, "requirement": scenario.get("requirement"),
                         "tier": scenario.get("tier"), "verdict": "invalid",
                         "what": f"no {missing} result file", "repaired": repaired})
            continue
        baseline, after = score_side(scenario, baseline_rec), score_side(scenario, after_rec)
        verdict = verdict_of(baseline, after, min_delta)
        row = {
            "id": sid, "requirement": scenario.get("requirement"), "tier": scenario.get("tier"),
            "model": after_rec.get("model"), "baseline": baseline, "after": after,
            "delta": round(after["rate"] - baseline["rate"], 4), "verdict": verdict,
            "repaired": repaired,
        }
        if verdict in ("unchanged", "regressed"):
            suspect = is_suspect(after)
            row["suspect"] = suspect
            finding = {
                "kind": verdict,
                "severity": "critical" if verdict == "regressed" else "major",
                "violated_criterion": f"{scenario.get('requirement')}/{sid}",
                "suspect": suspect,
                "what": f"baseline {baseline['passes']}/{baseline['valid']} -> after "
                        f"{after['passes']}/{after['valid']}; "
                        + "; ".join(r["what"] for f in after["failing"] for r in f["reasons"])[:400],
            }
            if suspect and not repaired:
                repairable.append(sid)
            else:
                findings.append(finding)
        rows.append(row)
    verdicts = [r["verdict"] for r in rows]
    if repairable:
        result = "repair"
    elif any(v in ("unchanged", "regressed") for v in verdicts):
        result = "fail"
    elif "invalid" in verdicts:
        result = "infra"
    else:
        result = "pass"
    return {
        "result": result,
        "min_delta": min_delta,
        "verdict_counts": {v: verdicts.count(v) for v in VERDICTS},
        "scenarios": rows,
        "findings": findings,
        "repairable": repairable,
    }


def baseline_only(scenarios, records, out_path):
    rows, infra = [], False
    for scenario in scenarios:
        record = records.get((scenario["id"], "baseline"))
        if record is None:
            rows.append({"id": scenario["id"], "state": "missing"})
            infra = True
            continue
        side = score_side(scenario, record)
        if not side["enough"]:
            state, infra = "invalid", True
        elif not side["absent"] and side["rate"] == 1.0:
            state = "saturated"
        else:
            state = "open"
        rows.append({"id": scenario["id"], "requirement": scenario.get("requirement"),
                     "state": state, "baseline": side})
    write_json(out_path, {"baseline_only": True, "scenarios": rows})
    for row in rows:
        side = row.get("baseline")
        if side:
            base = "absent" if side["absent"] else f"{side['passes']}/{side['valid']}"
            print(f"{row['id']}: baseline {base} [cache {side['cache']}] = {row['state']}")
        else:
            print(f"{row['id']}: {row['state']}")
        if row["state"] == "saturated":
            print(f"SATURATED_BASELINE: {row['id']}")
    print(f"RESULT: {'infra' if infra else 'pass'}")
    return 3 if infra else 0


def main(argv):
    if len(argv) == 3 and argv[1] == "--print-contract":
        if argv[2] != "verdicts":
            sys.stderr.write(f"unknown contract {argv[2]!r}\n")
            return 2
        print(json.dumps(VERDICTS))
        return 0

    parser = argparse.ArgumentParser(prog="evidence_merge.py")
    parser.add_argument("--scenarios-dir", required=True)
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--min-delta", type=float, default=0.0)
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--repairs-file",
                        help="JSON list of scenario ids already given one repair round "
                             "this attempt; a suspect scenario NOT in this list is pulled "
                             "into `repairable` (RESULT: repair) instead of `findings`")
    args = parser.parse_args(argv[1:])

    try:
        paths = sorted(glob.glob(os.path.join(args.scenarios_dir, "*.json")))
        if not paths:
            raise ValueError(f"no scenario files in {args.scenarios_dir}")
        scenarios = [load_scenario(p) for p in paths]
        records = {}
        for scenario in scenarios:
            for label in ("baseline", "after"):
                path = os.path.join(args.results_dir, f"{scenario['id']}.{label}.json")
                if os.path.isfile(path):
                    with open(path, encoding="utf-8") as fh:
                        records[(scenario["id"], label)] = json.load(fh)
        repairs = []
        if args.repairs_file:
            try:
                with open(args.repairs_file, encoding="utf-8") as fh:
                    repairs = json.load(fh)
            except FileNotFoundError:
                repairs = []  # no scenario has been repaired yet this attempt
            if not isinstance(repairs, list):
                raise ValueError(f"{args.repairs_file}: expected a JSON list of scenario ids")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    if args.baseline_only:
        return baseline_only(scenarios, records, args.out)

    merged = merge(scenarios, records, args.min_delta, repairs)
    write_json(args.out, merged)
    for row in merged["scenarios"]:
        if "baseline" in row:
            b, a = row["baseline"], row["after"]
            base = "absent" if b["absent"] else f"{b['passes']}/{b['valid']}"
            print(f"{row['id']} ({row['requirement']}, tier {row['tier']}): baseline {base} "
                  f"[cache {b['cache']}] -> after {a['passes']}/{a['valid']} "
                  f"[cache {a['cache']}] = {row['verdict']}"
                  + (" [suspect]" if row.get("suspect") else "")
                  + (" [repaired]" if row.get("repaired") else ""))
        else:
            print(f"{row['id']}: {row['verdict']} — {row['what']}")
    for sid in merged["repairable"]:
        print(f"REPAIR: {sid}")
    for row in merged["scenarios"]:
        if row.get("suspect") and row.get("repaired"):
            print(f"REPAIR_USED: {row['id']}")
    print(f"RESULT: {merged['result']}")
    return {"pass": 0, "fail": 1, "infra": 3, "repair": 4}[merged["result"]]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
