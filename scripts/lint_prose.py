#!/usr/bin/env python3
"""
Tier 0 — the free evidence every touched skill/agent file gets, always.

It checks only what is mechanically decidable about a prose file, i.e. what
something other than a human reads:

  crlf               a CR byte. Claude Code silently ignores a skill/agent
                     file with CRLF line endings — no error, the file simply
                     does not exist for the harness.
  frontmatter        skills/**/SKILL.md, agents/*.md and commands/*.md start
                     with a `---` block that parses as `key: value` lines;
                     skills and agents carry `name` and `description`; `name`
                     equals the skill's directory / the agent's file stem.
  missing-reference  every `${CLAUDE_PLUGIN_ROOT}/<path>` a file mentions
                     exists under the root.
  contract           a table the prose states about a script agrees with the
                     script. Marked in the prose as

                       <!-- ape:contract script=scripts/tier_select.py name=kinds -->
                       | kind | ... |
                       |---|---|
                       | `prose-step` | ... |
                       <!-- /ape:contract -->

                     The block's tokens (first-column backticked tokens of a
                     table; else every backticked token in the block) must
                     equal, as a set, the JSON list printed by
                     `<script> --print-contract <name>`.

It never judges wording. A phrase-presence check on a model-read file is the
thing this plugin exists to stop writing.

Usage:
  lint_prose.py --root <dir> [--all] [<file> ...] [--out <json>]

`--all` lints every skill, agent and command file under the root plus any
AGENTS.md / CLAUDE.md at its top. Exit 0 clean, 1 findings, 2 usage error.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ape_common import split_frontmatter  # noqa: E402

# A piped stdout on Windows defaults to the ANSI code page; every consumer of
# these scripts (the skill, the tests, CI) reads UTF-8.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

KEY_LINE = re.compile(r"^([A-Za-z][\w-]*):(\s|$)(.*)$")
PLUGIN_REF = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)")
# Anchored to a whole line: a sentence that merely mentions the marker in
# backticks (this repo's AGENTS.md does) is not a block.
CONTRACT_OPEN = re.compile(r"^\s*<!--\s*ape:contract\s+script=(\S+)\s+name=(\S+)\s*-->\s*$")
CONTRACT_CLOSE = re.compile(r"^\s*<!--\s*/ape:contract\s*-->\s*$")
BACKTICKED = re.compile(r"`([^`]+)`")
RUNNERS = {".py": [sys.executable], ".sh": ["bash"], ".mjs": ["node"], ".js": ["node"]}


def parse_frontmatter(block):
    """Minimal `key: value` parser with indented continuation lines. Returns
    (dict, error | None). Deliberately not a YAML parser: the harness is
    lenient about `: ` inside an unquoted description, strict YAML is not."""
    data, current = {}, None
    for number, line in enumerate(block.split("\n"), start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = KEY_LINE.match(line)
        if match:
            current = match.group(1)
            if current in data:
                return data, f"line {number}: duplicate key {current!r}"
            data[current] = match.group(3).strip()
        elif line[0] in " \t" and current is not None:
            data[current] = (data[current] + " " + line.strip()).strip()
        else:
            return data, f"line {number}: not a `key: value` line: {line[:60]!r}"
    return data, None


def file_role(rel):
    parts = rel.split("/")
    if len(parts) >= 3 and parts[-3] == "skills" and parts[-1] == "SKILL.md":
        return "skill"
    if len(parts) >= 2 and parts[-2] == "agents" and rel.endswith(".md"):
        return "agent"
    if len(parts) >= 2 and parts[-2] == "commands" and rel.endswith(".md"):
        return "command"
    return "other"


def contract_tokens(lines):
    rows = [l for l in lines if l.strip().startswith("|")]
    tokens = set()
    if rows:
        for row in rows:
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            if cells:
                tokens.update(BACKTICKED.findall(cells[0]))
    else:
        for line in lines:
            tokens.update(BACKTICKED.findall(line))
    return tokens


def script_contract(root, script, name):
    path = os.path.join(root, script)
    if not os.path.isfile(path):
        return None, f"contract script {script} does not exist"
    runner = RUNNERS.get(os.path.splitext(script)[1])
    if runner is None:
        return None, f"no runner known for {script}"
    try:
        proc = subprocess.run(runner + [path, "--print-contract", name], capture_output=True,
                              text=True, encoding="utf-8", timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"could not run {script}: {exc}"
    if proc.returncode != 0:
        return None, f"{script} --print-contract {name} exited {proc.returncode}: {proc.stderr.strip()[:200]}"
    try:
        values = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None, f"{script} --print-contract {name} did not print JSON"
    if not isinstance(values, list):
        return None, f"{script} --print-contract {name} did not print a JSON list"
    return {str(v) for v in values}, None


def lint_file(root, rel):
    findings = []

    def add(code, what, line=None):
        findings.append({"file": rel, "code": code, "line": line, "what": what})

    path = os.path.join(root, rel)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        add("unreadable", str(exc))
        return findings

    if b"\r" in raw:
        line = raw[:raw.index(b"\r")].count(b"\n") + 1
        add("crlf", "CR byte found; this file must be LF only or the harness ignores it", line)
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")

    role = file_role(rel)
    if role != "other":
        block, _body = split_frontmatter(text)
        if block is None:
            add("frontmatter", "no `---` frontmatter block at the top of the file", 1)
        else:
            data, error = parse_frontmatter(block)
            if error:
                add("frontmatter", error)
            if role in ("skill", "agent"):
                for key in ("name", "description"):
                    if not data.get(key):
                        add("frontmatter", f"missing or empty `{key}`")
                expected = rel.split("/")[-2] if role == "skill" else os.path.splitext(rel.split("/")[-1])[0]
                if data.get("name") and data["name"] != expected:
                    add("frontmatter", f"`name: {data['name']}` does not match {expected!r}")

    for number, line in enumerate(text.split("\n"), start=1):
        for ref in PLUGIN_REF.findall(line):
            ref = ref.rstrip(".")
            if not os.path.exists(os.path.join(root, ref)):
                add("missing-reference", f"${{CLAUDE_PLUGIN_ROOT}}/{ref} does not exist", number)

    lines = text.split("\n")
    index = 0
    while index < len(lines):
        opened = CONTRACT_OPEN.search(lines[index])
        if not opened:
            index += 1
            continue
        start = index
        index += 1
        block_lines = []
        while index < len(lines) and not CONTRACT_CLOSE.search(lines[index]):
            block_lines.append(lines[index])
            index += 1
        if index >= len(lines):
            add("contract", "ape:contract block is never closed", start + 1)
            break
        script, name = opened.group(1), opened.group(2)
        expected, error = script_contract(root, script, name)
        if error:
            add("contract", error, start + 1)
        else:
            stated = contract_tokens(block_lines)
            if stated != expected:
                missing = sorted(expected - stated)
                extra = sorted(stated - expected)
                add("contract",
                    f"table disagrees with {script} ({name}): missing {missing}, not in script {extra}",
                    start + 1)
        index += 1
    return findings


def discover(root):
    # hooks.json is no prose, but its ${CLAUDE_PLUGIN_ROOT} references break the
    # same way when a release stages too little, so --all covers it.
    patterns = ["skills/*/SKILL.md", "agents/*.md", "commands/*.md", "AGENTS.md", "CLAUDE.md",
                "hooks/hooks.json"]
    found = []
    for pattern in patterns:
        found.extend(glob.glob(os.path.join(root, pattern)))
    return sorted(os.path.relpath(p, root).replace("\\", "/") for p in found)


def main(argv):
    parser = argparse.ArgumentParser(prog="lint_prose.py")
    parser.add_argument("--root", required=True)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--out")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv[1:])

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        sys.stderr.write(f"--root {args.root} is not a directory\n")
        return 2
    files = []
    for item in args.files:
        absolute = item if os.path.isabs(item) else os.path.join(root, item)
        files.append(os.path.relpath(absolute, root).replace("\\", "/"))
    if args.all:
        files.extend(f for f in discover(root) if f not in files)
    if not files:
        sys.stderr.write("nothing to lint: pass files or --all\n")
        return 2

    findings = []
    for rel in files:
        findings.extend(lint_file(root, rel))
    result = {"files_checked": files, "findings": findings}
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(result, fh, indent=2)
            fh.write("\n")
    for f in findings:
        where = f"{f['file']}:{f['line']}" if f["line"] else f["file"]
        print(f"{where}: [{f['code']}] {f['what']}")
    print(f"TIER0: {'FAIL' if findings else 'OK'} ({len(files)} file(s), {len(findings)} finding(s))")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
