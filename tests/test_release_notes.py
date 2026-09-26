"""
Behaviour tests for #14: release notes are empty in GitHub Releases and never
sent to the marketplace.

`release_notes.py` computes notes from `main`'s history since the previous
release tag, recovering that tag's base commit from a `Source-Commit: <sha>`
trailer when present and an ancestor of `--head`, otherwise from a
date-based heuristic (the legacy tags v0.0.1..v0.0.4 carry no trailer).
`release_payload.sh` builds the marketplace dispatch JSON safely with `jq`.

Both scripts are exercised as real child processes against a real, disposable
git repository built commit by commit with deterministic dates (same
`GIT_COMMITTER_DATE`/`GIT_AUTHOR_DATE` pattern as
`test_code_state_is_the_commit_the_branch_had_at_the_time` in
test_evidence_pipeline.py) — never a string pin on `release.yml` itself
(R3/R4 in the plan are `ci-evidence`/`none` and get no test here).
"""
import json
import os
import re
import shutil
import subprocess

import pytest

from conftest import SCRIPTS, git, run_script

# --- git fixture helpers ----------------------------------------------------------------------


def _rev(repo, ref):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", ref],
                          capture_output=True, text=True, check=True).stdout.strip()


def _short(repo, ref):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", ref],
                          capture_output=True, text=True, check=True).stdout.strip()


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    return repo


def _dated_commit(repo, message, date):
    """One commit on the current branch, subject == message, at a fixed date."""
    (repo / "f.txt").write_text(message, encoding="utf-8")
    git(repo, "add", "-A")
    os.environ["GIT_COMMITTER_DATE"] = os.environ["GIT_AUTHOR_DATE"] = date
    try:
        git(repo, "commit", "-q", "-m", message)
    finally:
        del os.environ["GIT_COMMITTER_DATE"], os.environ["GIT_AUTHOR_DATE"]
    return _rev(repo, "HEAD")


def _orphan_release_commit(repo, tag, date, trailer=None):
    """The real shape release.yml produces: `git checkout --orphan release`,
    commit `release: <tag>` (optionally with a `Source-Commit:` trailer as a
    second `-m` paragraph), tag it, then return to main — exactly like the
    orphan branch is force-pushed and the tag is created on that commit."""
    branch = "orphan-" + re.sub(r"[^A-Za-z0-9]+", "-", tag)
    git(repo, "checkout", "-q", "--orphan", branch)
    git(repo, "rm", "-rf", "-q", ".")
    (repo / "STAGE.txt").write_text(tag, encoding="utf-8")
    git(repo, "add", "-A")
    args = ["commit", "-q", "-m", f"release: {tag}"]
    if trailer:
        args += ["-m", f"Source-Commit: {trailer}"]
    os.environ["GIT_COMMITTER_DATE"] = os.environ["GIT_AUTHOR_DATE"] = date
    try:
        git(repo, *args)
    finally:
        del os.environ["GIT_COMMITTER_DATE"], os.environ["GIT_AUTHOR_DATE"]
    sha = _rev(repo, "HEAD")
    git(repo, "tag", tag, sha)
    git(repo, "checkout", "-q", "main")
    return sha


def _unrelated_sha(repo):
    """A real commit sha that exists in the repo's object store but is not an
    ancestor of main — for the trailer-present-but-not-ancestor case."""
    git(repo, "checkout", "-q", "--orphan", "scratch-unrelated")
    git(repo, "rm", "-rf", "-q", ".")
    (repo / "x.txt").write_text("x\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "unrelated")
    sha = _rev(repo, "HEAD")
    git(repo, "checkout", "-q", "main")
    return sha


def _merge_commit(repo, subject, date, parent1, parent2):
    """A real merge commit (two parents) built with plumbing so it carries no
    content of its own — its tree equals parent1's — so `--no-merges` is the
    only thing that can exclude it from the notes."""
    tree = _rev(repo, f"{parent1}^{{tree}}")
    env = dict(os.environ, GIT_COMMITTER_DATE=date, GIT_AUTHOR_DATE=date)
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", "commit-tree", tree, "-p", parent1, "-p", parent2,
         "-m", subject],
        capture_output=True, text=True, env=env, check=True)
    sha = result.stdout.strip()
    git(repo, "update-ref", "refs/heads/main", sha)
    git(repo, "checkout", "-q", "main")
    return sha


def _subjects_and_shas(notes_text):
    parsed = []
    for line in notes_text.splitlines():
        if not line.strip():
            continue
        assert line.startswith("- ") and line.endswith(")"), line
        subject, _, rest = line[2:].rpartition(" (")
        parsed.append((subject, rest[:-1]))
    return parsed


def _notes(repo, *args, out):
    return run_script("release_notes.py", *args, "--out", out, cwd=repo)


# --- release_notes.py: R1 — notes since the previous orphan-tagged release ---------------------


def test_notes_since_orphan_tagged_release_list_only_main_changes_after_it(tmp_path):
    repo = _init_repo(tmp_path)
    _dated_commit(repo, "A", "2024-01-01T00:00:10Z")
    b_sha = _dated_commit(repo, "B", "2024-01-01T00:00:20Z")
    # legacy shape: orphan tag, dated after B, no Source-Commit trailer
    _orphan_release_commit(repo, "p--v0.0.1", "2024-01-01T00:00:30Z")
    c_sha = _dated_commit(repo, "C", "2024-01-01T00:00:40Z")
    d_sha = _dated_commit(repo, "D", "2024-01-01T00:00:50Z")
    # a real merge commit, reachable from head, that must never show up in notes
    _merge_commit(repo, "Merge branch 'old-b'", "2024-01-01T00:01:00Z", d_sha, b_sha)

    out = tmp_path / "notes.md"
    proc = _notes(repo, "--tag-prefix", "p--v", "--head", "main", out=out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PREVIOUS: p--v0.0.1" in proc.stdout
    assert f"BASE: {b_sha} (date)" in proc.stdout

    parsed = _subjects_and_shas(out.read_text(encoding="utf-8"))
    assert {subject for subject, _ in parsed} == {"C", "D"}
    shas = dict(parsed)
    assert shas["C"] == _short(repo, c_sha)
    assert shas["D"] == _short(repo, d_sha)


# --- release_notes.py: additional edge-case coverage --------------------------------------------


def test_source_commit_trailer_wins_over_date(tmp_path):
    """The orphan commit is dated after C but carries `Source-Commit: <B>` —
    the notes must be {C, D} through the trailer. A date-only fallback would
    instead recover C (the last main commit at/before the tag's date) and
    report only {D}, so this distinguishes the two mechanisms."""
    repo = _init_repo(tmp_path)
    _dated_commit(repo, "A", "2024-02-01T00:00:10Z")
    b_sha = _dated_commit(repo, "B", "2024-02-01T00:00:20Z")
    _dated_commit(repo, "C", "2024-02-01T00:00:40Z")
    _orphan_release_commit(repo, "p--v0.0.1", "2024-02-01T00:00:50Z", trailer=b_sha)
    _dated_commit(repo, "D", "2024-02-01T00:01:00Z")

    out = tmp_path / "notes.md"
    proc = _notes(repo, "--tag-prefix", "p--v", "--head", "main", out=out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"BASE: {b_sha} (trailer)" in proc.stdout
    parsed = _subjects_and_shas(out.read_text(encoding="utf-8"))
    assert {subject for subject, _ in parsed} == {"C", "D"}


def test_newest_of_two_release_tags_is_the_base(tmp_path):
    """p--v0.0.10 is tagged later in time than p--v0.0.9 even though it sorts
    before it as a plain string ('1' < '9') — the previous tag must be chosen
    by the tagged commit's committer date, never by tag-name string order."""
    repo = _init_repo(tmp_path)
    _dated_commit(repo, "A", "2024-03-01T00:00:10Z")
    b_sha = _dated_commit(repo, "B", "2024-03-01T00:00:20Z")
    _orphan_release_commit(repo, "p--v0.0.9", "2024-03-01T00:00:30Z", trailer=b_sha)
    c_sha = _dated_commit(repo, "C", "2024-03-01T00:00:40Z")
    _orphan_release_commit(repo, "p--v0.0.10", "2024-03-01T00:00:50Z", trailer=c_sha)
    _dated_commit(repo, "D", "2024-03-01T00:01:00Z")

    out = tmp_path / "notes.md"
    proc = _notes(repo, "--tag-prefix", "p--v", "--head", "main", out=out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PREVIOUS: p--v0.0.10" in proc.stdout
    assert f"BASE: {c_sha} (trailer)" in proc.stdout
    parsed = _subjects_and_shas(out.read_text(encoding="utf-8"))
    assert {subject for subject, _ in parsed} == {"D"}


def test_no_previous_tag_lists_all_history(tmp_path):
    repo = _init_repo(tmp_path)
    _dated_commit(repo, "A", "2024-04-01T00:00:10Z")
    _dated_commit(repo, "B", "2024-04-01T00:00:20Z")
    _dated_commit(repo, "C", "2024-04-01T00:00:30Z")

    out = tmp_path / "notes.md"
    proc = _notes(repo, "--tag-prefix", "p--v", "--head", "main", out=out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PREVIOUS: none" in proc.stdout
    parsed = _subjects_and_shas(out.read_text(encoding="utf-8"))
    assert {subject for subject, _ in parsed} == {"A", "B", "C"}


def test_no_changes_since_tag_is_not_blank(tmp_path):
    repo = _init_repo(tmp_path)
    _dated_commit(repo, "A", "2024-05-01T00:00:10Z")
    b_sha = _dated_commit(repo, "B", "2024-05-01T00:00:20Z")
    _orphan_release_commit(repo, "p--v0.0.1", "2024-05-01T00:00:30Z", trailer=b_sha)
    # no further commits on main: head is already at the tag's own base

    out = tmp_path / "notes.md"
    proc = _notes(repo, "--tag-prefix", "p--v", "--head", "main", out=out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PREVIOUS: p--v0.0.1" in proc.stdout
    assert out.read_text(encoding="utf-8") == "- No changes since p--v0.0.1.\n"


def test_trailer_present_but_not_ancestor_falls_back_to_date(tmp_path):
    """The tagged commit's message does carry a `Source-Commit:` trailer, but
    it names a real commit from an unrelated orphan branch, never merged into
    main — not an ancestor of `--head`. It must be rejected in favour of the
    date fallback, not trusted blindly."""
    repo = _init_repo(tmp_path)
    _dated_commit(repo, "A", "2024-06-01T00:00:10Z")
    b_sha = _dated_commit(repo, "B", "2024-06-01T00:00:20Z")
    foreign_sha = _unrelated_sha(repo)
    _orphan_release_commit(repo, "p--v0.0.1", "2024-06-01T00:00:30Z", trailer=foreign_sha)
    c_sha = _dated_commit(repo, "C", "2024-06-01T00:00:40Z")
    d_sha = _dated_commit(repo, "D", "2024-06-01T00:00:50Z")

    out = tmp_path / "notes.md"
    proc = _notes(repo, "--tag-prefix", "p--v", "--head", "main", out=out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"BASE: {b_sha} (date)" in proc.stdout
    parsed = _subjects_and_shas(out.read_text(encoding="utf-8"))
    assert {subject for subject, _ in parsed} == {"C", "D"}


# --- release_payload.sh: R2 — the payload stays valid JSON with hostile notes -------------------

NEEDS_BASH_AND_JQ = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="release_payload.sh needs bash and jq on PATH")


def run_bash(name, *args, env=None):
    full_env = dict(os.environ)
    full_env.update(env or {})
    bash = shutil.which("bash") or "bash"
    return subprocess.run([bash, str(SCRIPTS / name), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8",
                          env=full_env, timeout=55)


PAYLOAD_ENV = {
    "NAME": "agent-autonomous-prompt-engineer",
    "DESC": 'A plugin with "quotes" and a backslash \\ inside.',
    "REPO": "seretos-agents/agent-autonomous-prompt-engineer",
    "VERSION": "1.2.3",
    "TAG": "agent-autonomous-prompt-engineer--v1.2.3",
}


@NEEDS_BASH_AND_JQ
def test_payload_is_valid_json_for_quotes_newlines_backslashes(tmp_path):
    # hostile notes: an embedded quote, backslashes, an internal newline, and
    # a lone trailing backslash right before the file's own trailing newline
    notes = ('Fixed "bug" in \\legacy\\path.\n'
             'Second line with a lone trailing backslash: \\\n')
    notes_file = tmp_path / "notes.md"
    notes_file.write_bytes(notes.encode("utf-8"))

    proc = run_bash("release_payload.sh", notes_file, env=PAYLOAD_ENV)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)

    assert payload["event_type"] == "plugin-release"
    cp = payload["client_payload"]
    assert cp["name"] == PAYLOAD_ENV["NAME"]
    assert cp["description"] == PAYLOAD_ENV["DESC"]
    assert cp["repo"] == PAYLOAD_ENV["REPO"]
    assert cp["category"] == "skill"
    assert cp["version"] == PAYLOAD_ENV["VERSION"]
    assert cp["ref"] == PAYLOAD_ENV["TAG"]
    assert cp["icon"] == (f"https://raw.githubusercontent.com/{PAYLOAD_ENV['REPO']}/"
                          f"{PAYLOAD_ENV['TAG']}/assets/icon.png")
    assert cp["description_url"] == (f"https://raw.githubusercontent.com/{PAYLOAD_ENV['REPO']}/"
                                     f"{PAYLOAD_ENV['TAG']}/description.md")
    # --rawfile keeps the trailing newline: this must round-trip byte for byte.
    assert cp["notes"] == notes


@NEEDS_BASH_AND_JQ
def test_payload_handles_empty_description(tmp_path):
    notes_file = tmp_path / "notes.md"
    notes_file.write_text("- A (abc123)\n", encoding="utf-8")
    env = dict(PAYLOAD_ENV, DESC="")

    proc = run_bash("release_payload.sh", notes_file, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["client_payload"]["description"] == ""
