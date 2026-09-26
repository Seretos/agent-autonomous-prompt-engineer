#!/usr/bin/env python3
"""
Copies a run directory (`<worktree_path>/.ape/<package>-<attempt>/`) to a
place outside every git checkout, before the pipeline posts a terminal event.

Why: `worktree_remove` deletes the worktree — and everything under it,
including `.ape/` — the moment a `failed`/`blocked` attempt ends. On the real
incident this exists for (agent-project-issues#386, both attempts of
agent-autonomous-prompt-engineer#5), the only evidence that survived was the
orchestrator's own session transcript and, by luck, a still-live
`~/.claude/ape-cache/` entry — the run directory itself, with its scenario
files, evidence JSON and per-round history, was gone. Archiving it is
unconditional and happens before every terminal event, not only on a bad one:
a `ci-green` run is also worth keeping, and "only archive on failure" is a
branch nobody would remember to test.

Usage:
  rundir_archive.py --rundir <dir> --project <id> --package <id> --attempt <n>
Prints `ARCHIVE: <path>` and exits 0. Exits 1 if `--rundir` does not exist or
the resolved archive target sits inside a git checkout (refused the same way
`ape_common.fresh_workdir` refuses one), 2 on a usage error.
"""
import argparse
import datetime
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ape_common import canonical, inside_git_checkout  # noqa: E402

# A piped stdout on Windows defaults to the ANSI code page; every consumer of
# these scripts (the skill, the tests, CI) reads UTF-8.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")


def archive_root():
    return os.environ.get("APE_ARCHIVE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "ape-runs")


def main(argv):
    parser = argparse.ArgumentParser(prog="rundir_archive.py")
    parser.add_argument("--rundir", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args(argv[1:])

    if not os.path.isdir(args.rundir):
        sys.stderr.write(f"{args.rundir}: not a directory\n")
        return 1

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"{args.project}-{args.package}-{args.attempt}-{stamp}"
    target = os.path.join(archive_root(), name)

    if inside_git_checkout(target):
        sys.stderr.write(f"{target}: resolves inside a git checkout; refusing to archive "
                          "there — set APE_ARCHIVE_DIR to a directory outside any repository\n")
        return 1

    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    shutil.copytree(args.rundir, target)
    print(f"ARCHIVE: {canonical(target)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
