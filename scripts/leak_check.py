#!/usr/bin/env python3
"""
Decides whether a blind run stayed blind, from its own transcript.

Isolation is a property of how the process was started (ape_common.py), but a
started process can still reach outside: a consumer with `Read` can open any
absolute path on the machine. So every blind sample's stream-json transcript
is walked afterwards and the sample is marked INVALID — not "failed", invalid:
it is not evidence either way — when it

  foreign-mcp      started with an MCP server connected,
  foreign-plugin   started with a plugin other than the staged surface (or a
                   builtin one),
  foreign-tool     used a tool outside the allowlist of its surface kind,
  outside-read     pointed Read/Glob/Grep at a path outside the allowed roots
                   (the staged surface and its own empty cwd).

Mechanical, no model: a model deciding "that read was harmless" is the curator
this plugin does not have.

Usage:
  leak_check.py <transcript.jsonl> --allow-root <dir> [--allow-root <dir> ...]
                [--staged-plugin <name>] [--tools Skill,Read,Glob,Grep] [--cwd <dir>]
  leak_check.py --print-contract reasons
Exit 0 valid, 1 invalid (reasons on stdout), 2 usage error.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ape_common import is_inside  # noqa: E402

# A piped stdout on Windows defaults to the ANSI code page; every consumer of
# these scripts (the skill, the tests, CI) reads UTF-8.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

REASONS = ["foreign-mcp", "foreign-plugin", "foreign-tool", "outside-read"]
# The CLI adds this one itself whenever --json-schema is passed.
ALWAYS_ALLOWED_TOOLS = {"StructuredOutput"}
PATH_FIELDS = {"Read": ["file_path"], "Glob": ["path", "pattern"], "Grep": ["path"]}


def parse_transcript(text):
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def tool_uses(events):
    for event in events:
        if event.get("type") != "assistant":
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                yield block.get("name", ""), block.get("input") or {}


def check(events, allowed_roots, allowed_tools, staged_plugin=None, cwd=None):
    """Returns a list of {reason, what}; empty means the sample is valid.
    `cwd` is the sample's own working directory: a relative path the consumer
    used resolves against it, not against this checker's cwd."""
    problems = []
    allowed = set(allowed_tools) | ALWAYS_ALLOWED_TOOLS

    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            for server in event.get("mcp_servers") or []:
                name = server.get("name") if isinstance(server, dict) else str(server)
                problems.append({"reason": "foreign-mcp", "what": f"MCP server connected: {name}"})
            for plugin in event.get("plugins") or []:
                name = plugin.get("name") if isinstance(plugin, dict) else str(plugin)
                builtin = isinstance(plugin, dict) and plugin.get("path") == "builtin"
                if not builtin and name != staged_plugin:
                    problems.append({"reason": "foreign-plugin", "what": f"plugin loaded: {name}"})

    for name, tool_input in tool_uses(events):
        if name not in allowed:
            problems.append({"reason": "foreign-tool", "what": f"used tool {name}"})
            continue
        for field in PATH_FIELDS.get(name, []):
            value = tool_input.get(field)
            if not isinstance(value, str) or not value:
                continue
            # A Glob pattern is only a path claim when it is absolute.
            if field == "pattern" and not os.path.isabs(value) and not value.startswith(("/", "~")):
                if ".." not in value.replace("\\", "/").split("/"):
                    continue
            target = os.path.expanduser(value)
            if not os.path.isabs(target) and cwd:
                target = os.path.join(cwd, target)
            if not any(is_inside(target, root) for root in allowed_roots):
                problems.append({"reason": "outside-read", "what": f"{name}({field}={value})"})
    return problems


def main(argv):
    if len(argv) == 3 and argv[1] == "--print-contract":
        if argv[2] != "reasons":
            sys.stderr.write(f"unknown contract {argv[2]!r}\n")
            return 2
        print(json.dumps(REASONS))
        return 0

    parser = argparse.ArgumentParser(prog="leak_check.py")
    parser.add_argument("transcript")
    parser.add_argument("--allow-root", action="append", default=[])
    parser.add_argument("--staged-plugin")
    parser.add_argument("--tools", default="")
    parser.add_argument("--cwd")
    args = parser.parse_args(argv[1:])
    try:
        with open(args.transcript, encoding="utf-8", errors="replace") as fh:
            events = parse_transcript(fh.read())
    except OSError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    tools = [t for t in args.tools.split(",") if t]
    problems = check(events, args.allow_root, tools, args.staged_plugin, args.cwd)
    for p in problems:
        print(f"[{p['reason']}] {p['what']}")
    print(f"LEAK_CHECK: {'INVALID' if problems else 'VALID'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
