#!/usr/bin/env python3
"""
Runs ONE scenario N times against ONE version of the artifact under test, each
sample in its own isolated `claude -p` process, and records what came back.

It judges nothing. Scoring and the baseline-vs-after delta are
evidence_merge.py's job; this script only produces samples and says which of
them are valid evidence.

ISOLATION (ticket item 5). Each sample runs with
  * the flag set of ape_common.ISOLATION_FLAGS (no user/project settings, no
    MCP servers),
  * cwd = a fresh empty directory outside every repository,
  * only the surface under test made available — never the repo:
      skill        the listed files are copied into a throw-away plugin and
                   exposed with --plugin-dir, so discovery runs through the
                   harness's real skill listing; tools: Skill, Read, Glob, Grep
      agent        the consumer sees only each agent's `name` + `description`
                   (that IS the surface a dispatching session sees); no tools
      doc          the documentation text is handed over verbatim; no tools
      agent-step   tier 1: the file's body is the system prompt, the case
                   input (case_builder.py output) is the user turn; no tools
  * --model pinned explicitly (default: sonnet — the weakest model the surface
    is meant for), so a run never inherits whatever `/model` was last set to,
  * a leak check over the sample's own transcript (leak_check.py): a sample
    that reached outside the surface is INVALID, which is neither pass nor fail.

COST (ticket item 3). Results are cached under APE_CACHE_DIR (default
~/.claude/ape-cache), keyed by a hash of the artifact under test plus the
scenario's input, the model and the sample count. The baseline of a ticket is
stable across every writer round and every retry, so after the first run a
ticket pays only for "after". A hit is reported as `CACHE: hit`. An artifact
that does not exist at the requested ref is recorded as `absent` and costs
nothing: a surface that is not there is found by nobody.

Usage:
  blind_run.py --scenario <json> --root <worktree> --label <baseline|after>
               --out-dir <dir> [--git-ref <ref>] [--samples 3] [--model sonnet]
               [--timeout 420] [--no-cache]

`--git-ref` reads the artifact from that ref (`git show <ref>:<path>`) instead
of the working tree — that is how the baseline is taken without a checkout.

Writes <out-dir>/<scenario-id>.<label>.json (+ one .jsonl transcript per
sample). Exit 0 when every sample is valid evidence, 1 when any sample is
invalid or crashed (the file is still written — report an infrastructure
round, do not read a partial result as a complete one), 2 on a usage error.
"""
import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import leak_check  # noqa: E402
from ape_common import (canonical, fresh_workdir, run_isolated, sha256_bytes,  # noqa: E402
                        split_frontmatter, write_json)
from lint_prose import parse_frontmatter  # noqa: E402
from scenario_validate import case_input_path, load_scenario, validate  # noqa: E402

# A piped stdout on Windows defaults to the ANSI code page; every consumer of
# these scripts (the skill, the tests, CI) reads UTF-8.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

RUNNER_VERSION = "1"
STAGED_PLUGIN = "surface-under-test"
ARGV_PROMPT_LIMIT = 24000
TOOLS_BY_KIND = {
    "skill": ["Skill", "Read", "Glob", "Grep"],
    "agent": [],
    "doc": [],
    "agent-step": [],
}
CONSUMER_SCHEMA = {
    "type": "object",
    "properties": {
        "could_complete": {"type": "boolean"},
        "selected": {"type": "string",
                     "description": "the skill, subagent type or tool you relied on, or \"none\""},
        "answer": {"type": "string"},
        "missing": {"type": "array", "items": {"type": "string"},
                    "description": "information you needed and were not given"},
        "confusing": {"type": "array", "items": {"type": "string"},
                      "description": "things that were there but hard to use correctly"},
    },
    "required": ["could_complete", "selected", "answer", "missing", "confusing"],
}
CONSUMER_BRIEF = (
    "Do the task below using only what is available to you in this session. "
    "Do not guess facts you were not given: if something you need is missing, "
    "say so instead of inventing it.\n\n")


def read_artifact(root, rel, git_ref):
    """bytes, or None when the file does not exist at that version."""
    rel = rel.replace("\\", "/")
    if git_ref:
        proc = subprocess.run(["git", "-C", root, "show", f"{git_ref}:{rel}"], capture_output=True)
        return proc.stdout if proc.returncode == 0 else None
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


def staged_rel(rel):
    """Where a surface file sits inside the staged plugin: from its last
    `skills/` or `agents/` segment on, so a plugin kept in a subdirectory of
    the repo stages the same way as one at the root."""
    parts = rel.replace("\\", "/").split("/")
    for anchor in ("skills", "agents"):
        if anchor in parts:
            index = len(parts) - 1 - parts[::-1].index(anchor)
            return "/".join(parts[index:])
    return "/".join(parts[-1:])


def stage_plugin(artifacts):
    plugin_dir = fresh_workdir("ape-surface-")
    os.makedirs(os.path.join(plugin_dir, ".claude-plugin"))
    write_json(os.path.join(plugin_dir, ".claude-plugin", "plugin.json"),
               {"name": STAGED_PLUGIN, "version": "0.0.0", "description": "surface under test"})
    for rel, data in artifacts.items():
        target = os.path.join(plugin_dir, staged_rel(rel))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(data)
    return plugin_dir


def text_of(data):
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n")


def build_prompt(kind, scenario, artifacts):
    """(stdin text, system prompt | None)."""
    if kind == "agent-step":
        with open(case_input_path(scenario), encoding="utf-8") as fh:
            case = fh.read()
        bodies = [split_frontmatter(text_of(d))[1] for d in artifacts.values()]
        return case, "\n\n".join(bodies)
    task = scenario["task"]
    if kind == "skill":
        return CONSUMER_BRIEF + "TASK:\n" + task, None
    if kind == "agent":
        lines = ["Subagent types you can dispatch (name — description):"]
        for data in artifacts.values():
            block, _body = split_frontmatter(text_of(data))
            fm, _err = parse_frontmatter(block or "")
            lines.append(f"- {fm.get('name', '?')} — {fm.get('description', '')}")
        lines.append("")
        lines.append("Name the one you would dispatch in `selected` and put the prompt you "
                     "would give it in `answer`.")
        return CONSUMER_BRIEF + "\n".join(lines) + "\n\nTASK:\n" + task, None
    sections = [f"=== {rel} ===\n{text_of(data)}" for rel, data in artifacts.items()]
    return (CONSUMER_BRIEF + "This documentation is everything you have:\n\n"
            + "\n\n".join(sections) + "\n\nTASK:\n" + task), None


def run_sample(index, kind, scenario, artifacts, model, timeout, out_prefix):
    workdir = fresh_workdir("ape-blind-")
    plugin_dir = stage_plugin(artifacts) if kind == "skill" else None
    transcript_path = f"{out_prefix}.sample{index}.jsonl"
    try:
        stdin_text, system_prompt = build_prompt(kind, scenario, artifacts)
        tools = TOOLS_BY_KIND[kind]
        args = ["--model", model, "--tools", ",".join(tools),
                "--permission-mode", "bypassPermissions", "--no-session-persistence",
                "--output-format", "stream-json", "--verbose"]
        if plugin_dir:
            args += ["--plugin-dir", plugin_dir]
        else:
            args += ["--disable-slash-commands"]
        prompt_via = "n/a"
        if system_prompt is not None and len(system_prompt) > ARGV_PROMPT_LIMIT:
            # A Windows command line ends at 32767 characters. Past the limit
            # the step's instructions travel in the user turn instead; the
            # sample records that, because it is a (small) loss of fidelity.
            stdin_text = ("YOUR INSTRUCTIONS FOR THIS STEP:\n" + system_prompt
                          + "\n\nINPUT:\n" + stdin_text)
            prompt_via = "stdin"
        elif system_prompt is not None:
            args += ["--system-prompt", system_prompt]
            prompt_via = "argv"
        else:
            args += ["--json-schema", json.dumps(CONSUMER_SCHEMA)]

        argv, rc, stdout, stderr = run_isolated(args, stdin_text, workdir, timeout)
        with open(transcript_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(stdout)

        events = leak_check.parse_transcript(stdout)
        roots = [workdir] + ([plugin_dir] if plugin_dir else [])
        problems = leak_check.check(events, roots, tools, STAGED_PLUGIN, cwd=workdir)

        result = next((e for e in reversed(events) if e.get("type") == "result"), None)
        crashed = rc != 0 or result is None or bool(result.get("is_error"))
        if crashed:
            problems.append({"reason": "crashed",
                             "what": f"exit {rc}; {stderr.strip()[-300:] or 'no result event'}"})

        texts, selected = [], []
        for event in events:
            if event.get("type") != "assistant":
                continue
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif block.get("type") == "tool_use" and block.get("name") == "Skill":
                    selected.append(str((block.get("input") or {}).get("skill", "")))
        structured = (result or {}).get("structured_output")
        if isinstance(structured, dict):
            texts.append(str(structured.get("answer", "")))
            if kind != "skill" and structured.get("selected"):
                selected.append(str(structured["selected"]))
        elif result and isinstance(result.get("result"), str) and result["result"] not in texts:
            texts.append(result["result"])

        return {
            "sample": index,
            "valid": not problems,
            "invalid_reasons": problems,
            # `selected` for a skill comes from the transcript's Skill calls,
            # not from what the consumer says it used.
            "selected": selected,
            "answer_text": "\n".join(t for t in texts if t),
            "structured": structured if isinstance(structured, dict) else None,
            "cost_usd": (result or {}).get("total_cost_usd"),
            "system_prompt_via": prompt_via,
            "transcript": canonical(transcript_path),
            "argv": [a if len(a) < 200 else a[:200] + "…" for a in argv],
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        if plugin_dir:
            shutil.rmtree(plugin_dir, ignore_errors=True)


def cache_dir():
    return os.environ.get("APE_CACHE_DIR") or os.path.join(os.path.expanduser("~"), ".claude", "ape-cache")


def main(argv):
    parser = argparse.ArgumentParser(prog="blind_run.py")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--label", required=True, choices=["baseline", "after"])
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--git-ref")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args(argv[1:])

    try:
        scenario = load_scenario(args.scenario)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    problems = validate(scenario)
    if problems:
        for p in problems:
            sys.stderr.write(f"[{p['code']}] {p['what']}\n")
        sys.stderr.write("refusing to pay for a scenario scenario_validate.py rejects\n")
        return 2
    if args.samples < 1:
        sys.stderr.write("--samples must be >= 1\n")
        return 2

    kind = scenario["surface"]["kind"]
    paths = [p.replace("\\", "/") for p in scenario["surface"]["paths"]]
    found = {p: read_artifact(args.root, p, args.git_ref) for p in paths}
    artifacts = {p: d for p, d in found.items() if d is not None}

    os.makedirs(args.out_dir, exist_ok=True)
    out_prefix = os.path.join(args.out_dir, f"{scenario['id']}.{args.label}")
    out_path = out_prefix + ".json"

    hasher_input = [RUNNER_VERSION, kind, args.model, str(args.samples), scenario.get("task") or ""]
    if kind == "agent-step":
        with open(case_input_path(scenario), "rb") as fh:
            hasher_input.append(sha256_bytes(fh.read()))
    artifact_sha = sha256_bytes(b"\x00".join(
        p.encode() + b"\x00" + (found[p] if found[p] is not None else b"\x00absent") for p in paths))
    cache_key = sha256_bytes(("\x00".join(hasher_input) + "\x00" + artifact_sha).encode())

    record = {
        "scenario": scenario["id"], "requirement": scenario.get("requirement"),
        "tier": scenario.get("tier"), "label": args.label, "kind": kind,
        "model": args.model, "git_ref": args.git_ref or "",
        "artifact_sha256": artifact_sha, "cache_key": cache_key,
        "absent": not artifacts, "cache": "miss", "samples": [],
    }

    cache_path = os.path.join(cache_dir(), cache_key + ".json")
    if not artifacts:
        record["cache"] = "n/a"
    elif not args.no_cache and os.path.isfile(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            cached = json.load(fh)
        record["samples"] = cached["samples"]
        record["cache"] = "hit"
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.samples) as pool:
            futures = [pool.submit(run_sample, i + 1, kind, scenario, artifacts,
                                   args.model, args.timeout, out_prefix)
                       for i in range(args.samples)]
            record["samples"] = [f.result() for f in futures]
        # Only a complete, fully valid run is worth replaying later.
        if all(s["valid"] for s in record["samples"]) and not args.no_cache:
            os.makedirs(cache_dir(), exist_ok=True)
            write_json(cache_path, {"samples": record["samples"]})

    write_json(out_path, record)
    invalid = [s for s in record["samples"] if not s["valid"]]
    print(f"RESULT_FILE: {canonical(out_path)}")
    print(f"CACHE: {record['cache']}")
    print(f"ARTIFACT: {'absent' if record['absent'] else artifact_sha[:12]}")
    print(f"SAMPLES: {len(record['samples'])} run, {len(invalid)} invalid")
    for s in invalid:
        for p in s["invalid_reasons"]:
            print(f"  sample {s['sample']}: [{p['reason']}] {p['what']}")
    return 1 if invalid else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
