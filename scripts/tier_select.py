#!/usr/bin/env python3
"""
Chooses the evidence tier of every requirement — mechanically.

Inputs are the planner's declared kind per requirement and the paths involved
(declared at plan time, actually changed after the writer ran). There is no
model here and no judgment about content: a model that picks its own evidence
tier picks the cheap one. The table is fixed:

  kind            tiers   meaning
  prose-step      0, 1    the agent/skill of ONE workflow step changed -> step replay
  prose-surface   0, 2    a surface changed (a skill/agent `description`, tool
                          documentation) -> blind test
  prose-other     0       model-read prose with no step and no surface
                          (rationale, AGENTS.md background) -> lint only; listed
                          in the PR's "Not covered by tests" section
  script          -       decidable, belongs in a script with behaviour tests.
                          NOT this plugin's work: it never writes product code.
                          Reported as `foreign_requirements`; the skill turns a
                          non-empty list into a `blocked` event (split the package).

Tier 0 (lint) applies to every touched prose file, always, free.

Violations (exit 1) — each one is a blocking finding, not a warning:
  prose-declared-touches-code   a prose-* requirement lists, or the diff of a
                                package of only prose-* requirements contains,
                                a path that is not prose
  step-kind-without-step-file   prose-step without a path under agents/ or skills/
  unknown-kind / malformed      the requirements file does not follow the table

Usage:
  tier_select.py --requirements <json> [--changed-file <paths.txt>]
                 [--worktree <path> --base <ref>] [--out <json>]
  tier_select.py --print-contract <kinds|prose-suffixes>

`--changed-file` lists changed paths one per line; `--worktree/--base` derives
them from git instead (committed since base, working tree, untracked). Without
either, only the declared paths are judged — that is the plan-time call.
"""
import argparse
import json
import posixpath
import subprocess
import sys

# A piped stdout on Windows defaults to the ANSI code page; every consumer of
# these scripts (the skill, the tests, CI) reads UTF-8.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

KINDS = {
    "prose-step": [0, 1],
    "prose-surface": [0, 2],
    "prose-other": [0],
    "script": [],
}
# A path is prose when a model (or a human) reads it and no program executes
# or parses it for behaviour. Everything else is code for this purpose —
# including YAML/JSON/TOML: a workflow or manifest is machine-read.
PROSE_SUFFIXES = [".md", ".markdown", ".txt", ".prompt"]
STEP_ROOTS = ("agents/", "skills/")
SCRATCH_ROOTS = (".ape/", ".adev/")

CONTRACTS = {"kinds": list(KINDS), "prose-suffixes": PROSE_SUFFIXES}


def norm(path):
    path = posixpath.normpath(path.strip().replace("\\", "/"))
    return path[2:] if path.startswith("./") else path


def is_prose(path):
    return any(path.lower().endswith(s) for s in PROSE_SUFFIXES)


def is_scratch(path):
    return any(path.startswith(root) for root in SCRATCH_ROOTS)


def git_changed(worktree, base):
    def run(*args):
        proc = subprocess.run(["git", "-C", worktree, *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return [line for line in proc.stdout.splitlines() if line.strip()]
    paths = set(run("diff", "--name-only", f"{base}...HEAD"))
    paths.update(run("diff", "--name-only", "HEAD"))
    paths.update(run("ls-files", "--others", "--exclude-standard"))
    return sorted(paths)


def select(requirements, changed=None):
    violations, warnings, out_reqs, foreign = [], [], [], []
    claimed = {}

    for index, req in enumerate(requirements):
        rid = str(req.get("id") or f"#{index + 1}")
        kind = req.get("kind")
        paths = [norm(p) for p in (req.get("paths") or [])]
        if kind not in KINDS:
            violations.append({"code": "unknown-kind", "requirement": rid, "path": "",
                               "what": f"kind {kind!r} is not one of {list(KINDS)}"})
            continue
        if not paths:
            violations.append({"code": "malformed", "requirement": rid, "path": "",
                               "what": "a requirement must name the paths it changes"})
        for path in paths:
            claimed.setdefault(path, []).append((rid, kind))
            if kind != "script" and not is_prose(path):
                violations.append({
                    "code": "prose-declared-touches-code", "requirement": rid, "path": path,
                    "what": f"declared {kind} but {path} is not a prose file"})
        if kind == "prose-step" and not any(p.startswith(STEP_ROOTS) for p in paths):
            violations.append({
                "code": "step-kind-without-step-file", "requirement": rid, "path": "",
                "what": "prose-step needs the agent or skill file of the step it changes"})
        if kind == "script":
            foreign.append(rid)
        out_reqs.append({"id": rid, "kind": kind, "tiers": KINDS[kind], "paths": paths})

    touched = set(claimed)
    if changed is not None:
        changed = [p for p in (norm(c) for c in changed) if p and not is_scratch(p)]
        touched = set(changed)
        for path in changed:
            owners = claimed.get(path, [])
            if not is_prose(path) and not any(kind == "script" for _rid, kind in owners):
                if not any(v["path"] == path for v in violations):
                    violations.append({
                        "code": "prose-declared-touches-code", "requirement": "", "path": path,
                        "what": f"the diff touches {path}, which is not prose, and no "
                                "`script` requirement claims it"})
            elif not owners:
                warnings.append({"code": "unclaimed-prose-path", "path": path,
                                 "what": "changed, but no requirement names it (tier 0 still applies)"})
        for path in claimed:
            if path not in touched:
                warnings.append({"code": "declared-path-unchanged", "path": path,
                                 "what": "a requirement names it, the diff does not touch it"})

    return {
        "tier0_files": sorted(p for p in touched if is_prose(p)),
        "requirements": out_reqs,
        "foreign_requirements": foreign,
        "violations": violations,
        "warnings": warnings,
    }


def main(argv):
    if len(argv) == 3 and argv[1] == "--print-contract":
        if argv[2] not in CONTRACTS:
            sys.stderr.write(f"unknown contract {argv[2]!r}\n")
            return 2
        print(json.dumps(CONTRACTS[argv[2]]))
        return 0

    parser = argparse.ArgumentParser(prog="tier_select.py")
    parser.add_argument("--requirements", required=True)
    parser.add_argument("--changed-file")
    parser.add_argument("--worktree")
    parser.add_argument("--base")
    parser.add_argument("--out")
    args = parser.parse_args(argv[1:])

    try:
        with open(args.requirements, encoding="utf-8") as fh:
            payload = json.load(fh)
        requirements = payload.get("requirements")
        if not isinstance(requirements, list) or not requirements:
            raise ValueError("'requirements' must be a non-empty list")
        changed = None
        if args.changed_file:
            with open(args.changed_file, encoding="utf-8") as fh:
                changed = fh.read().splitlines()
        elif args.worktree or args.base:
            if not (args.worktree and args.base):
                raise ValueError("--worktree and --base go together")
            changed = git_changed(args.worktree, args.base)
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    result = select(requirements, changed)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")
    print(text)
    for v in result["violations"]:
        sys.stderr.write(f"VIOLATION {v['code']}: {v['what']}\n")
    return 1 if result["violations"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
