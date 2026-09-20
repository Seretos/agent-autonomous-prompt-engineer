#!/usr/bin/env python3
"""
Rebuilds the input ONE workflow step saw in a real past case, from history
that already exists. It archives nothing and stores nothing: the ticket's
comments carry `created_at`, session transcripts sit under ~/.claude/projects/,
the code state is in git. A replay corpus would be a second copy of all three
that starts rotting the day it is written.

What it assembles, as of `--at`:
  * the ticket (title, labels, body) and every comment created before the time,
  * the latest `<!-- adev:event v1 -->` block among those comments, parsed,
  * the slice of the package's session transcript(s) up to the time,
  * the commit the branch pointed at (`--repo` + `--ref`), when given.

STATED LIMITS — printed as `LIMIT:` lines and written into the output whenever
they apply, because a rebuilt input that silently differs from the real one is
worse than none:
  * A ticket body edited after the time cannot be reconstructed; bodies are not
    versioned. The ticket's `updated_at` is later than the time in nearly every
    real case (every comment bumps it), so this limit is stated whenever the
    ticket file cannot prove otherwise (`body_edited_at`, if present, decides).
  * A comment edited after the time is included with today's body and flagged.
  * A transcript folder that is absent on this machine is exit 4, "not
    available", and NO output file — never a partial input that looks whole.
    Pass --no-transcript to build a ticket-only input on purpose.

The ticket comes from a file, not from the network: the JSON the project-issues
MCP's `get_ticket` returns (`{"ticket": {...}, "comments": [...]}`), saved by
the caller. This script has no provider code and needs no token.

Usage:
  case_builder.py --ticket-file <json> --at <ISO-8601> --step <name> --out <file>
                  [--package <id>] [--projects-dir <dir>] [--transcript-match <substr>]
                  [--no-transcript] [--max-transcript-chars 40000]
                  [--repo <path> --ref <branch-or-sha>]
Exit 0 built, 4 transcript not available, 2 usage/input error.
"""
import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys

# A piped stdout on Windows defaults to the ANSI code page; every consumer of
# these scripts (the skill, the tests, CI) reads UTF-8.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

EVENT_MARKER = "<!-- adev:event"
EVENT_BLOCK = re.compile(r"<!--\s*adev:event[^\n]*\n(.*?)-->", re.DOTALL)


def parse_time(value):
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    stamp = datetime.datetime.fromisoformat(text)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return stamp


def parse_event(body):
    match = EVENT_BLOCK.search(body or "")
    if not match:
        return None
    event = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            event[key.strip()] = value.strip()
    return event


def select_comments(comments, at):
    kept, limits = [], []
    for comment in comments:
        created = comment.get("created_at")
        if not created or parse_time(created) > at:
            continue
        updated = comment.get("updated_at")
        edited_later = bool(updated) and parse_time(updated) > at
        if edited_later:
            limits.append(f"comment {comment.get('id')} was edited after {at.isoformat()}; "
                          "its body as of then cannot be reconstructed (today's body is shown)")
        kept.append(dict(comment, _edited_later=edited_later))
    kept.sort(key=lambda c: parse_time(c["created_at"]))
    return kept, limits


def latest_event(comments):
    for comment in reversed(comments):
        if EVENT_MARKER in (comment.get("body") or ""):
            event = parse_event(comment["body"])
            if event:
                return event, comment
    return None, None


def body_limit(ticket, at):
    edited = ticket.get("body_edited_at")
    if edited:
        if parse_time(edited) > at:
            return (f"the ticket body was edited at {edited}, after the time; bodies are not "
                    "versioned, so the body shown is today's, not the one the step saw")
        return None
    updated = ticket.get("updated_at")
    if updated and parse_time(updated) > at:
        return (f"the ticket was updated at {updated}, after the time, and the ticket file does not "
                "say whether that touched the body; bodies are not versioned, so the body shown "
                "is today's and may differ from the one the step saw")
    return None


def render_entry(entry):
    """One transcript record as text, or '' when it carries nothing a step
    input needs (hook attachments, queue operations, cost records)."""
    kind = entry.get("type")
    if kind not in ("user", "assistant"):
        return ""
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return f"[{kind}] {content}"
    parts = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            parts.append(f"<tool_use {block.get('name')}: "
                         f"{json.dumps(block.get('input'), ensure_ascii=False)[:600]}>")
        elif block.get("type") == "tool_result":
            inner = block.get("content")
            if isinstance(inner, list):
                inner = " ".join(str(i.get("text", "")) for i in inner if isinstance(i, dict))
            parts.append(f"<tool_result: {str(inner)[:600]}>")
    text = "\n".join(p for p in parts if p)
    return f"[{kind}] {text}" if text else ""


def transcript_slice(projects_dir, match, at, max_chars):
    """(sessions, None) or (None, reason-not-available)."""
    if not os.path.isdir(projects_dir):
        return None, f"transcript directory {projects_dir} does not exist on this machine"
    folders = sorted(d for d in glob.glob(os.path.join(projects_dir, "*"))
                     if os.path.isdir(d) and match in os.path.basename(d))
    if not folders:
        return None, f"no transcript folder matching {match!r} under {projects_dir}"
    sessions = []
    for folder in folders:
        for path in sorted(glob.glob(os.path.join(folder, "*.jsonl"))):
            rendered, first, last = [], None, None
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    stamp = entry.get("timestamp")
                    if not stamp:
                        continue
                    try:
                        when = parse_time(stamp)
                    except ValueError:
                        continue
                    if when > at:
                        break
                    first = first or stamp
                    last = stamp
                    text = render_entry(entry)
                    if text:
                        rendered.append(text)
            if first:
                sessions.append({"folder": os.path.basename(folder),
                                 "session": os.path.splitext(os.path.basename(path))[0],
                                 "first": first, "last": last, "text": "\n\n".join(rendered)})
    if not sessions:
        return None, f"transcript folders match {match!r}, but no session started before {at.isoformat()}"
    sessions.sort(key=lambda s: parse_time(s["first"]))
    # The step saw the END of the history, so the budget is spent from the tail.
    budget = max_chars
    for session in reversed(sessions):
        text = session["text"]
        if len(text) > budget:
            session["text"] = ("[… earlier part cut to fit --max-transcript-chars …]\n"
                               + text[len(text) - budget:]) if budget > 0 else ""
            session["cut"] = True
        budget = max(0, budget - len(session["text"]))
    return sessions, None


def code_state(repo, ref, at):
    proc = subprocess.run(
        ["git", "-C", repo, "rev-list", "-1", f"--before={at.isoformat()}", ref],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    sha = proc.stdout.strip()
    if proc.returncode != 0 or not sha:
        return None, (f"no commit on {ref} before the time in {repo} "
                      f"({proc.stderr.strip()[:200] or 'empty history'})")
    show = subprocess.run(["git", "-C", repo, "show", "-s", "--format=%H%n%cI%n%s", sha],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    full, when, subject = (show.stdout.strip().split("\n") + ["", "", ""])[:3]
    return {"sha": full or sha, "committed": when, "subject": subject, "ref": ref}, None


def build(ticket_payload, at, step, package, sessions, code, limits):
    # Ticket bodies, comments and transcripts carry markdown headings of their
    # own; the `[case]` tag keeps this document's structure tellable from theirs.
    ticket = ticket_payload.get("ticket") or {}
    comments = ticket_payload["_comments"]
    event, event_comment = latest_event(comments)
    out = [f"# [case] Input — step `{step}`, package {package}, as of {at.isoformat()}", ""]
    out += ["# [case] Limits of this reconstruction", ""]
    out += [f"- {limit}" for limit in limits] or ["- none apply"]
    out += ["", f"# [case] Ticket {ticket.get('id', package)} — {ticket.get('title', '')}", "",
            f"labels: {', '.join(ticket.get('labels') or [])}", "", ticket.get("body") or "", ""]
    out += [f"# [case] Comments created before the time ({len(comments)})", ""]
    for comment in comments:
        flag = " — EDITED LATER, body is today's" if comment["_edited_later"] else ""
        out += [f"## [case] comment {comment.get('id')} by {comment.get('author')} "
                f"({comment.get('created_at')}){flag}", "", comment.get("body") or "", ""]
    out += ["# [case] Latest adev:event before the time", ""]
    if event:
        out += ["```", json.dumps(event, indent=2), "```",
                f"(from comment {event_comment.get('id')}, {event_comment.get('created_at')})", ""]
    else:
        out += ["none", ""]
    out += ["# [case] Session transcript slice", ""]
    if sessions is None:
        out += ["not requested (--no-transcript)", ""]
    else:
        for s in sessions:
            out += [f"## [case] session {s['session']} ({s['first']} … {s['last']}) in {s['folder']}",
                    "", s["text"], ""]
    out += ["# [case] Code state", ""]
    out += ([f"{code['ref']} @ {code['sha']} ({code['committed']}) — {code['subject']}", ""]
            if code else ["not requested or not available", ""])
    return "\n".join(out)


def main(argv):
    parser = argparse.ArgumentParser(prog="case_builder.py")
    parser.add_argument("--ticket-file", required=True)
    parser.add_argument("--at", required=True)
    parser.add_argument("--step", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--package")
    parser.add_argument("--projects-dir",
                        default=os.path.join(os.path.expanduser("~"), ".claude", "projects"))
    parser.add_argument("--transcript-match")
    parser.add_argument("--no-transcript", action="store_true")
    parser.add_argument("--max-transcript-chars", type=int, default=40000)
    parser.add_argument("--repo")
    parser.add_argument("--ref")
    args = parser.parse_args(argv[1:])

    try:
        at = parse_time(args.at)
        with open(args.ticket_file, encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict) or "ticket" not in payload:
            raise ValueError("--ticket-file must hold a get_ticket response: {\"ticket\": …, \"comments\": […]}")
        if bool(args.repo) != bool(args.ref):
            raise ValueError("--repo and --ref go together")
        ticket = payload["ticket"]
        package = args.package or str(ticket.get("id", ""))
        comments, limits = select_comments(payload.get("comments") or [], at)
        payload["_comments"] = comments
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    limit = body_limit(ticket, at)
    if limit:
        limits.insert(0, limit)

    sessions = None
    if not args.no_transcript:
        match = args.transcript_match or f"pkg-{package}-"
        sessions, reason = transcript_slice(args.projects_dir, match, at, args.max_transcript_chars)
        if sessions is None:
            print(f"NOT AVAILABLE: {reason}")
            print("No output written: a step input without the session transcript is a partial "
                  "input. Pass --no-transcript to build a ticket-only input deliberately.")
            return 4
        if any(s.get("cut") for s in sessions):
            limits.append("the transcript slice was cut from the front to fit "
                          f"--max-transcript-chars {args.max_transcript_chars}")

    code = None
    if args.repo:
        code, reason = code_state(args.repo, args.ref, at)
        if code is None:
            limits.append(f"code state not available: {reason}")

    text = build(payload, at, args.step, package, sessions, code, limits)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    for limit in limits:
        print(f"LIMIT: {limit}")
    event, _ = latest_event(comments)
    print(f"CASE_INPUT: {os.path.abspath(args.out).replace(os.sep, '/')}")
    print(f"COMMENTS: {len(comments)}  LATEST_EVENT: {(event or {}).get('event', 'none')}  "
          f"SESSIONS: {len(sessions) if sessions is not None else 'skipped'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
