"""
Behaviour tests for the two hooks: each is started the way the harness starts
it — a node process with the hook payload on stdin — inside and outside a
process-prompt-engineer run (`<cwd>/.ape/`).
"""
import json
import shutil
import subprocess

import pytest

from conftest import REPO, git

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_hook(name, payload):
    return subprocess.run([NODE, str(REPO / "hooks" / name)], input=json.dumps(payload),
                          capture_output=True, text=True, encoding="utf-8", timeout=30)


def bash_payload(cwd, command, agent_type=None, **tool_input):
    payload = {"cwd": str(cwd), "tool_name": "Bash", "tool_input": dict(tool_input, command=command)}
    if agent_type:
        payload["agent_type"] = agent_type
    return payload


def test_backgrounded_command_is_refused_inside_a_run(tmp_path):
    (tmp_path / ".ape").mkdir()
    for payload in (bash_payload(tmp_path, "python blind_run.py", run_in_background=True),
                    bash_payload(tmp_path, "nohup python blind_run.py &"),
                    {"cwd": str(tmp_path), "tool_name": "Monitor", "tool_input": {}}):
        proc = run_hook("check-no-background.mjs", payload)
        assert proc.returncode == 2 and "[ape-no-background]" in proc.stderr


def test_foreground_command_passes_inside_a_run(tmp_path):
    (tmp_path / ".ape").mkdir()
    proc = run_hook("check-no-background.mjs", bash_payload(tmp_path, "git status && echo 2>&1"))
    assert proc.returncode == 0 and proc.stderr == ""


def test_a_humans_session_keeps_background_commands(tmp_path):
    proc = run_hook("check-no-background.mjs", bash_payload(tmp_path, "npm run dev", run_in_background=True))
    assert proc.returncode == 0


def test_this_plugins_subagents_are_covered_without_the_run_directory(tmp_path):
    ours = bash_payload(tmp_path, "sleep 5", "agent-autonomous-prompt-engineer:prose-writer",
                        run_in_background=True)
    theirs = bash_payload(tmp_path, "sleep 5", "some-other-plugin:helper", run_in_background=True)
    assert run_hook("check-no-background.mjs", ours).returncode == 2
    assert run_hook("check-no-background.mjs", theirs).returncode == 0


def test_every_agent_this_plugin_ships_is_in_the_hooks_scope():
    """agents/ and the hook's PLUGIN_AGENTS are read by different programs
    (the harness, node); a new agent missing from the set is silently unguarded."""
    shipped = sorted(p.stem for p in (REPO / "agents").glob("*.md"))
    for name in shipped:
        payload = bash_payload("/nonexistent", "sleep 5", f"agent-autonomous-prompt-engineer:{name}",
                               run_in_background=True)
        assert run_hook("check-no-background.mjs", payload).returncode == 2, name


def _worktree(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = tmp_path / "wt"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / ".gitignore").write_text(".ape/\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "-u", "origin", "main")
    (repo / ".ape").mkdir()
    return repo


def test_turn_end_is_blocked_while_the_worktree_holds_work_no_remote_has(tmp_path):
    repo = _worktree(tmp_path)
    (repo / "agents.md").write_text("unsaved prose\n", encoding="utf-8")
    proc = run_hook("check-session-turn-end.mjs", {"cwd": str(repo)})
    assert json.loads(proc.stdout)["decision"] == "block"

    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "change")
    assert json.loads(run_hook("check-session-turn-end.mjs", {"cwd": str(repo)}).stdout)["decision"] == "block"

    git(repo, "push", "-q", "origin", "main")
    assert run_hook("check-session-turn-end.mjs", {"cwd": str(repo)}).stdout == ""


def test_turn_end_hook_blocks_once_and_never_outside_a_run(tmp_path):
    repo = _worktree(tmp_path)
    (repo / "agents.md").write_text("unsaved prose\n", encoding="utf-8")
    assert run_hook("check-session-turn-end.mjs", {"cwd": str(repo), "stop_hook_active": True}).stdout == ""
    shutil.rmtree(repo / ".ape")
    assert run_hook("check-session-turn-end.mjs", {"cwd": str(repo)}).stdout == ""
