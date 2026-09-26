#!/usr/bin/env python3
"""
Computes release notes from the checked-out history since the previous
release tag, to be written to a file before `release.yml` switches its
workspace to the orphan `release` branch (see #14).

Every tag this project creates points at a parentless `release: vX` commit on
the orphan branch, not at a commit on `main` — so the previous tag's base on
`main` has to be recovered rather than read off a parent pointer:

  1. The **previous tag** is the newest `refs/tags/<prefix>*`, by the tagged
     commit's committer date (never by tag-name string order — "0.0.10"
     sorts before "0.0.9" as a string).
  2. Its **base commit** on `--head` is the `Source-Commit: <sha>` trailer in
     the tagged commit's message, when one is present and is an ancestor of
     `--head`. Releases made after this package carry that trailer; the
     legacy tags (v0.0.1..v0.0.4) do not, and a trailer that names a commit
     from an unrelated history must not be trusted blindly either — both
     fall back to the newest first-parent commit on `--head` whose committer
     date is at or before the tagged commit's committer date.
  3. The **notes** are `git log --no-merges --format='- %s (%h)' <base>..<head>`,
     written to `--out`. No previous tag means head's whole history. An empty
     range (nothing changed) writes a one-line placeholder instead of an
     empty file.

Usage:
  release_notes.py --tag-prefix <prefix> [--head <ref>] --out <file>
Prints `PREVIOUS: <tag|none>` and, only when a previous tag was found,
`BASE: <sha> (trailer|date)`, then exits 0. Exits 1 on a git failure, 2 on a
usage error.
"""
import argparse
import re
import subprocess
import sys

# A piped stdout on Windows defaults to the ANSI code page; every consumer of
# these scripts (the skill, the tests, CI) reads UTF-8.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

TRAILER_RE = re.compile(r"^Source-Commit:\s*(\S+)\s*$", re.MULTILINE)


class GitFailure(RuntimeError):
    pass


def _git(args):
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8")


def _git_ok(args):
    result = _git(args)
    if result.returncode != 0:
        raise GitFailure((result.stderr or result.stdout).strip() or f"git {' '.join(args)} failed")
    return result.stdout


def find_previous_tag(tag_prefix):
    """Newest `refs/tags/<prefix>*`, by the tagged commit's committer date."""
    pattern = f"refs/tags/{tag_prefix}*"
    out = _git_ok(["for-each-ref", "--sort=-committerdate", "--format=%(refname:short)", pattern])
    lines = [line for line in out.splitlines() if line.strip()]
    return lines[0] if lines else None


def _trailer_sha(tag):
    body = _git_ok(["log", "-1", "--format=%B", tag])
    match = TRAILER_RE.search(body)
    return match.group(1) if match else None


def _is_ancestor(sha, head):
    return _git(["merge-base", "--is-ancestor", sha, head]).returncode == 0


def _base_by_date(tag, head):
    """Newest first-parent commit on `head` at or before the tagged commit's
    committer date — the fallback for a tag with no trailer, or one whose
    trailer is not an ancestor of `head`."""
    committer_date = _git_ok(["log", "-1", "--format=%cI", tag]).strip()
    out = _git_ok(["rev-list", "-1", "--first-parent", f"--before={committer_date}", head]).strip()
    if not out:
        raise GitFailure(f"no commit on {head} at or before {tag}'s committer date {committer_date}")
    return out


def resolve_base(tag, head):
    """Returns (base_sha, source) where source is "trailer" or "date"."""
    trailer_sha = _trailer_sha(tag)
    if trailer_sha and _is_ancestor(trailer_sha, head):
        return trailer_sha, "trailer"
    return _base_by_date(tag, head), "date"


def _notes_body(rev_range):
    body = _git_ok(["log", "--no-merges", "--format=- %s (%h)", rev_range])
    return body if body == "" or body.endswith("\n") else body + "\n"


def _write(out_path, text):
    with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main(argv):
    parser = argparse.ArgumentParser(prog="release_notes.py")
    parser.add_argument("--tag-prefix", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv[1:])

    try:
        previous_tag = find_previous_tag(args.tag_prefix)

        if previous_tag is None:
            # Branch 1: nothing to diff against — head's whole history.
            print("PREVIOUS: none")
            _write(args.out, _notes_body(args.head))
            return 0

        base_sha, source = resolve_base(previous_tag, args.head)
        print(f"PREVIOUS: {previous_tag}")
        print(f"BASE: {base_sha} ({source})")

        body = _notes_body(f"{base_sha}..{args.head}")
        if body.strip() == "":
            # Branch 2: the range since the previous release is empty — never
            # write a blank notes body.
            _write(args.out, f"- No changes since {previous_tag}.\n")
        else:
            # Branch 3: the ordinary case — one or more real commits.
            _write(args.out, body)
        return 0
    except GitFailure as exc:
        sys.stderr.write(f"release_notes.py: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
