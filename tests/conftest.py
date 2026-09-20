"""
Shared harness. Every script is exercised the way the pipeline uses it: as a
child process with real files and real arguments. Nothing here asserts that a
sentence exists in a skill or agent file — those are prose and carry no tests.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
FIXTURES = REPO / "tests" / "fixtures"
FAKE_CLAUDE = REPO / "tests" / "fake_claude.py"


def run_script(name, *args, env=None, cwd=None):
    full_env = dict(os.environ)
    full_env.update(env or {})
    return subprocess.run([sys.executable, str(SCRIPTS / name), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8",
                          env=full_env, cwd=cwd, timeout=55)


def write_json(path, payload):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")
    return path


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                   check=True, capture_output=True)


@pytest.fixture
def fake_env(tmp_path):
    """Environment that points the scripts at the fake CLI, a private cache
    and a log of every invocation."""
    log = tmp_path / "fake-claude-log"
    env = {
        "APE_CLAUDE_CMD": json.dumps([sys.executable, str(FAKE_CLAUDE)]),
        "APE_CACHE_DIR": str(tmp_path / "cache"),
        "FAKE_CLAUDE_LOG": str(log),
    }

    def invocations():
        if not log.exists():
            return []
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(log.iterdir())]

    env_obj = type("FakeEnv", (), {"env": env, "invocations": staticmethod(invocations)})
    return env_obj
