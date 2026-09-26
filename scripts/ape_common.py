"""
Shared helpers for this plugin's scripts. No model in here, no judgment: path
handling, hashing, and the one place that knows how an isolated `claude -p`
process is started.

ISOLATION (copied as a contract from agent-autonomous-developer's critic
runners, not imported): a plain `claude -p` started in an empty directory still
inherits user-level plugins, their skills, their MCP servers and their hooks,
and reads a CLAUDE.md in its own cwd. `--setting-sources ""` and
`--strict-mcp-config` switch that off; the cwd is a fresh temp directory
OUTSIDE any repository, because CLAUDE.md discovery walks parent directories.
Every isolated process of this plugin — blind consumer, step replay, scenario
critic — is started through `run_isolated`, so the flag set cannot drift
between them.
"""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile

# Present, in this order, in every isolated invocation. tests/ assert this
# against the argv a fake `claude` actually receives.
ISOLATION_FLAGS = ["--setting-sources", "", "--strict-mcp-config"]


def claude_cmd():
    """argv prefix that starts the CLI. APE_CLAUDE_CMD (a JSON list) replaces
    it — the behaviour tests point it at a fake that records its argv."""
    override = os.environ.get("APE_CLAUDE_CMD")
    if override:
        cmd = json.loads(override)
        if not isinstance(cmd, list) or not cmd:
            raise ValueError("APE_CLAUDE_CMD must be a non-empty JSON list")
        return [str(c) for c in cmd]
    return [shutil.which("claude") or "claude"]


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    with open(path, "rb") as fh:
        return sha256_bytes(fh.read())


def canonical(path):
    """Absolute path with forward slashes: `C:/Users/…`, which Git Bash and
    native Windows tools resolve identically (a bare `/tmp/x` they do not)."""
    return os.path.abspath(path).replace("\\", "/")


def is_inside(path, root):
    path = os.path.normcase(os.path.realpath(path))
    root = os.path.normcase(os.path.realpath(root))
    return path == root or path.startswith(root.rstrip("\\/") + os.sep)


def inside_git_checkout(path):
    """Whether `path` or one of its parents holds a `.git` — walked upward
    the same way CLAUDE.md discovery walks parents, so this catches exactly
    what would defeat that isolation."""
    probe = os.path.abspath(path)
    while True:
        if os.path.exists(os.path.join(probe, ".git")):
            return True
        parent = os.path.dirname(probe)
        if parent == probe:
            return False
        probe = parent


def fresh_workdir(prefix="ape-"):
    """An empty directory outside every repository. Refuses a temp dir that
    sits inside a git checkout — that would defeat the isolation silently."""
    workdir = tempfile.mkdtemp(prefix=prefix)
    if inside_git_checkout(workdir):
        shutil.rmtree(workdir, ignore_errors=True)
        raise RuntimeError(
            f"temp directory {workdir} is inside a git checkout; "
            "set TMPDIR/TEMP to a directory outside any repository")
    return workdir


def run_isolated(extra_args, stdin_text, cwd, timeout):
    """Start one isolated `claude -p` process in the foreground and wait for
    it. Returns (argv, returncode, stdout, stderr); a timeout is returncode
    124 with the partial output, never an exception."""
    argv = claude_cmd() + ["-p"] + ISOLATION_FLAGS + list(extra_args)
    try:
        proc = subprocess.run(
            argv, input=stdin_text, cwd=cwd, timeout=timeout,
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        return argv, proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return argv, 124, out, err + f"\ntimeout after {timeout}s"


def split_frontmatter(text):
    """(frontmatter_text | None, body). Frontmatter is the block between a
    leading `---` line and the next `---` line."""
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end == -1:
        if text.endswith("\n---"):
            return text[4:-4], ""
        return None, text
    return text[4:end], text[end + 5:]


def write_json(path, payload):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
