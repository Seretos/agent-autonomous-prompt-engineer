"""
Behaviour tests for the evidence path: leak check, blind runner (arguments,
isolation, cache, baseline from a git ref), the baseline-vs-after merge, the
scenario-critic runner, and the case builder on the real incident.
"""
import json
import os

from conftest import FIXTURES, git, run_script, write_json

OLD_SKILL = ("---\nname: units\ndescription: Helps with measurements.\n---\n"
             "Convert carefully.\n")
NEW_SKILL = ("---\nname: units\ndescription: Converts furlongs to meters. Use for old length units.\n---\n"
             "One furlong is 201.168 meters.\n")


def _repo_with_skill(tmp_path):
    repo = tmp_path / "repo"
    (repo / "skills" / "units").mkdir(parents=True)
    (repo / "skills" / "units" / "SKILL.md").write_bytes(OLD_SKILL.encode())
    (repo / "README.md").write_text("project readme — must never reach a blind run\n", encoding="utf-8")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    (repo / "skills" / "units" / "SKILL.md").write_bytes(NEW_SKILL.encode())
    return repo


def _skill_scenario(tmp_path):
    return write_json(tmp_path / "scenarios" / "S1.json", {
        "id": "S1", "requirement": "R1", "tier": 2,
        "surface": {"kind": "skill", "paths": ["skills/units/SKILL.md"]},
        "task": "How many meters are three of those old horse-racing lengths?",
        "expect": {"selects": "units", "matches": [r"603\.5"]}})


def _blind(fake_env, scenario, repo, label, out_dir, *extra, mode="ok"):
    env = dict(fake_env.env, FAKE_CLAUDE_MODE=mode)
    return run_script("blind_run.py", "--scenario", scenario, "--root", repo, "--label", label,
                      "--out-dir", out_dir, "--samples", "3", *extra, env=env)


# --- leak_check.py --------------------------------------------------------------------------


def test_a_real_blind_transcript_is_valid(tmp_path):
    """Fixture: a live `claude -p` run against a staged skill (paths scrubbed)."""
    proc = run_script("leak_check.py", FIXTURES / "blind-skill-transcript.jsonl",
                      "--allow-root", tmp_path, "--staged-plugin", "surface-under-test",
                      "--tools", "Skill,Read,Glob,Grep")
    assert proc.returncode == 0, proc.stdout
    assert "LEAK_CHECK: VALID" in proc.stdout


def test_reads_outside_the_surface_foreign_tools_and_mcp_invalidate_a_sample(tmp_path):
    staged = tmp_path / "staged"
    staged.mkdir()
    events = [
        {"type": "system", "subtype": "init", "mcp_servers": [{"name": "project-issues"}],
         "plugins": [{"name": "surface-under-test", "path": str(staged)},
                     {"name": "agent-worktree", "path": "C:/plugins/agent-worktree"}]},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": str(staged / "skills/x/SKILL.md")}},
            {"type": "tool_use", "name": "Read", "input": {"file_path": str(tmp_path / "repo" / "AGENTS.md")}},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "../../elsewhere.md"}},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}}]
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    proc = run_script("leak_check.py", transcript, "--allow-root", staged, "--cwd", staged,
                      "--staged-plugin", "surface-under-test", "--tools", "Skill,Read")
    assert proc.returncode == 1
    reasons = [line.split("]")[0].lstrip("[") for line in proc.stdout.splitlines() if line.startswith("[")]
    assert reasons == ["foreign-mcp", "foreign-plugin", "outside-read", "outside-read", "foreign-tool"]


# --- blind_run.py ---------------------------------------------------------------------------


def test_blind_run_starts_isolated_processes_that_see_only_the_surface(tmp_path, fake_env):
    repo = _repo_with_skill(tmp_path)
    proc = _blind(fake_env, _skill_scenario(tmp_path), repo, "after", tmp_path / "evidence",
                  "--model", "haiku")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = fake_env.invocations()
    assert len(calls) == 3
    for call in calls:
        argv = call["argv"]
        assert argv[0] == "-p"
        # isolation: no settings sources, no MCP, model pinned explicitly
        assert argv[argv.index("--setting-sources") + 1] == ""
        assert "--strict-mcp-config" in argv
        assert argv[argv.index("--model") + 1] == "haiku"
        assert argv[argv.index("--tools") + 1] == "Skill,Read,Glob,Grep"
        # an empty working directory that is not the repo (or inside it)
        assert call["cwd_listing"] == []
        assert not os.path.realpath(call["cwd"]).startswith(os.path.realpath(repo))
        # the staged plugin holds the surface and a synthetic manifest — nothing else of the repo
        assert sorted(call["plugin_files"]) == [".claude-plugin/plugin.json", "skills/units/SKILL.md"]
        assert call["plugin_files"]["skills/units/SKILL.md"] == NEW_SKILL
    record = json.loads((tmp_path / "evidence" / "S1.after.json").read_text(encoding="utf-8"))
    assert [s["selected"] for s in record["samples"]] == [["surface-under-test:units"]] * 3
    # the temp dirs are gone afterwards
    assert not any(os.path.exists(c["cwd"]) for c in calls)


def test_baseline_is_read_from_the_git_ref_not_from_the_working_tree(tmp_path, fake_env):
    repo = _repo_with_skill(tmp_path)
    proc = _blind(fake_env, _skill_scenario(tmp_path), repo, "baseline", tmp_path / "evidence",
                  "--git-ref", "main")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert {c["plugin_files"]["skills/units/SKILL.md"] for c in fake_env.invocations()} == {OLD_SKILL}


def test_second_run_of_an_unchanged_artifact_is_a_reported_cache_hit_and_costs_nothing(tmp_path, fake_env):
    repo = _repo_with_skill(tmp_path)
    scenario = _skill_scenario(tmp_path)
    first = _blind(fake_env, scenario, repo, "baseline", tmp_path / "e1", "--git-ref", "main")
    second = _blind(fake_env, scenario, repo, "baseline", tmp_path / "e2", "--git-ref", "main")
    assert "CACHE: miss" in first.stdout and "CACHE: hit" in second.stdout
    assert len(fake_env.invocations()) == 3  # the second run started no process
    # a changed artifact is a different key
    third = _blind(fake_env, scenario, repo, "after", tmp_path / "e3")
    assert "CACHE: miss" in third.stdout and len(fake_env.invocations()) == 6


def test_an_artifact_absent_at_the_ref_is_recorded_as_absent_and_not_run(tmp_path, fake_env):
    repo = _repo_with_skill(tmp_path)
    scenario = write_json(tmp_path / "scenarios" / "S2.json", {
        "id": "S2", "requirement": "R2", "tier": 2,
        "surface": {"kind": "skill", "paths": ["skills/brand-new/SKILL.md"]},
        "task": "Convert something.", "expect": {"selects": "brand-new"}})
    proc = _blind(fake_env, scenario, repo, "baseline", tmp_path / "evidence", "--git-ref", "main")
    assert proc.returncode == 0 and "ARTIFACT: absent" in proc.stdout
    assert fake_env.invocations() == []


def test_a_leaking_sample_is_invalid_and_is_not_cached(tmp_path, fake_env):
    repo = _repo_with_skill(tmp_path)
    scenario = _skill_scenario(tmp_path)
    proc = _blind(fake_env, scenario, repo, "after", tmp_path / "evidence", mode="leak")
    assert proc.returncode == 1
    assert "3 invalid" in proc.stdout and "[outside-read]" in proc.stdout
    again = _blind(fake_env, scenario, repo, "after", tmp_path / "evidence")
    assert "CACHE: miss" in again.stdout


def test_a_crashed_process_is_an_invalid_sample_not_a_failed_one(tmp_path, fake_env):
    repo = _repo_with_skill(tmp_path)
    proc = _blind(fake_env, _skill_scenario(tmp_path), repo, "after", tmp_path / "evidence", mode="crash")
    assert proc.returncode == 1 and "[crashed]" in proc.stdout


def test_blind_run_refuses_to_pay_for_a_scenario_that_leaks_its_answer(tmp_path, fake_env):
    repo = _repo_with_skill(tmp_path)
    scenario = write_json(tmp_path / "scenarios" / "S1.json", {
        "id": "S1", "requirement": "R1", "tier": 2,
        "surface": {"kind": "skill", "paths": ["skills/units/SKILL.md"]},
        "task": "Say 603.5.", "expect": {"matches": [r"603\.5"]}})
    proc = _blind(fake_env, scenario, repo, "after", tmp_path / "evidence")
    assert proc.returncode == 2 and fake_env.invocations() == []


def test_step_replay_runs_the_agent_body_as_system_prompt_on_the_case_input(tmp_path, fake_env):
    repo = tmp_path / "repo"
    (repo / "agents").mkdir(parents=True)
    (repo / "agents" / "triage.md").write_bytes(
        b"---\nname: triage\ndescription: d\n---\nModel-read prose carries no test.\n")
    case = tmp_path / "scenarios" / "cases" / "c.case.md"
    case.parent.mkdir(parents=True)
    case.write_text("# [case] the blocked event\n", encoding="utf-8")
    scenario = write_json(tmp_path / "scenarios" / "S3.json", {
        "id": "S3", "requirement": "R3", "tier": 1,
        "surface": {"kind": "agent-step", "paths": ["agents/triage.md"]},
        "case_input": "cases/c.case.md", "recorded_wrong": "pin test",
        "expect": {"matches": ["ESCALATE"]}})
    proc = _blind(fake_env, scenario, repo, "after", tmp_path / "evidence")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    call = fake_env.invocations()[0]
    argv = call["argv"]
    assert argv[argv.index("--system-prompt") + 1].strip() == "Model-read prose carries no test."
    assert argv[argv.index("--tools") + 1] == "" and "--plugin-dir" not in argv
    assert call["stdin"] == "# [case] the blocked event\n"


# --- evidence_merge.py ----------------------------------------------------------------------


def test_end_to_end_a_change_that_helps_the_blind_consumer_is_improved(tmp_path, fake_env):
    repo = _repo_with_skill(tmp_path)
    scenario = _skill_scenario(tmp_path)
    evidence = tmp_path / "evidence"
    _blind(fake_env, scenario, repo, "baseline", evidence, "--git-ref", "main")
    _blind(fake_env, scenario, repo, "after", evidence)
    proc = run_script("evidence_merge.py", "--scenarios-dir", tmp_path / "scenarios",
                      "--results-dir", evidence, "--out", tmp_path / "merged.json")
    assert proc.returncode == 0, proc.stdout
    assert "baseline 0/3" in proc.stdout and "after 3/3" in proc.stdout and "RESULT: pass" in proc.stdout
    row = json.loads((tmp_path / "merged.json").read_text(encoding="utf-8"))["scenarios"][0]
    assert row["verdict"] == "improved" and row["delta"] == 1.0


def _samples(*passing):
    return [{"sample": i + 1, "valid": True, "selected": [], "structured": None,
             "answer_text": "STATUS: ESCALATE" if ok else "STATUS: ANSWERED add a pin test"}
            for i, ok in enumerate(passing)]


def _merge_case(tmp_path, baseline, after, invalid_after=0):
    write_json(tmp_path / "scenarios" / "S1.json", {
        "id": "S1", "requirement": "R1", "tier": 2, "surface": {"kind": "doc", "paths": ["d.md"]},
        "task": "decide", "expect": {"matches": ["ESCALATE"], "forbids": ["pin test"]}})
    write_json(tmp_path / "results" / "S1.baseline.json", {"samples": _samples(*baseline), "cache": "hit"})
    if after is not None:
        samples = _samples(*after)
        for sample in samples[:invalid_after]:
            sample["valid"] = False
        write_json(tmp_path / "results" / "S1.after.json", {"samples": samples})
    proc = run_script("evidence_merge.py", "--scenarios-dir", tmp_path / "scenarios",
                      "--results-dir", tmp_path / "results", "--out", tmp_path / "merged.json")
    return proc, json.loads((tmp_path / "merged.json").read_text(encoding="utf-8"))


def test_the_pass_rule_is_a_delta_a_high_but_unchanged_score_fails(tmp_path):
    proc, merged = _merge_case(tmp_path, baseline=(True, True, False), after=(True, True, False))
    assert proc.returncode == 1 and merged["result"] == "fail"
    assert merged["scenarios"][0]["verdict"] == "unchanged"
    # shaped for stagnation_check.py's `evidence` gate
    assert merged["findings"][0]["kind"] == "unchanged"
    assert merged["findings"][0]["violated_criterion"] == "R1/S1"


def test_a_low_score_that_improved_passes(tmp_path):
    proc, merged = _merge_case(tmp_path, baseline=(False, False, False), after=(True, False, False))
    assert proc.returncode == 0 and merged["scenarios"][0]["verdict"] == "improved"


def test_a_regression_is_a_critical_finding(tmp_path):
    proc, merged = _merge_case(tmp_path, baseline=(True, True, False), after=(True, False, False))
    assert proc.returncode == 1 and merged["findings"][0]["severity"] == "critical"


def test_a_scenario_the_old_text_already_passes_is_saturated_not_evidence(tmp_path):
    proc, merged = _merge_case(tmp_path, baseline=(True, True, True), after=(True, True, True))
    assert proc.returncode == 0 and merged["scenarios"][0]["verdict"] == "saturated"
    assert merged["verdict_counts"]["improved"] == 0


def test_too_few_valid_samples_is_infrastructure_not_a_verdict(tmp_path):
    proc, merged = _merge_case(tmp_path, baseline=(False, False, False), after=(True, True, True),
                               invalid_after=2)
    assert proc.returncode == 3 and merged["result"] == "infra"


def test_baseline_only_names_saturated_scenarios_so_no_after_run_is_paid_for(tmp_path):
    _merge_case(tmp_path, baseline=(True, True, True), after=None)
    proc = run_script("evidence_merge.py", "--scenarios-dir", tmp_path / "scenarios", "--results-dir",
                      tmp_path / "results", "--out", tmp_path / "baseline.json", "--baseline-only")
    assert proc.returncode == 0 and "SATURATED_BASELINE: S1" in proc.stdout
    # …and the later full merge does not mistake the deliberately missing "after" for a crash
    proc, merged = _merge_case(tmp_path, baseline=(True, True, True), after=None)
    assert proc.returncode == 0 and merged["scenarios"][0]["verdict"] == "saturated"


# --- scenario_critic_run.py -----------------------------------------------------------------


def _critic(tmp_path, fake_env, critique=None, mode="ok"):
    (tmp_path / "spec.md").write_text("# 1 ticket\nthe spec\n", encoding="utf-8")
    (tmp_path / "plan.md").write_text("the plan\n", encoding="utf-8")
    env = dict(fake_env.env, FAKE_CLAUDE_MODE=mode)
    if critique is not None:
        env["FAKE_CLAUDE_CRITIQUE"] = json.dumps(critique)
    return run_script("scenario_critic_run.py", "--spec", tmp_path / "spec.md", "--plan", tmp_path / "plan.md",
                      "--scenarios-dir", tmp_path / "scenarios", "--out-dir", tmp_path / "critic", env=env)


def test_critic_runs_isolated_without_tools_on_a_verbatim_package(tmp_path, fake_env):
    _skill_scenario(tmp_path)
    critique = {"findings": [{"id": "F1", "scenario": "S1", "kind": "plan-level", "severity": "critical",
                              "violated_criterion": "the spec", "what": "no real case exists"}],
                "solid": [], "unverifiable_without_codebase_access": []}
    proc = _critic(tmp_path, fake_env, critique)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "GATE_RESULT: OK" in proc.stdout and "PLAN_LEVEL: yes" in proc.stdout
    call = fake_env.invocations()[0]
    argv = call["argv"]
    assert argv[argv.index("--setting-sources") + 1] == "" and "--strict-mcp-config" in argv
    assert "--disable-slash-commands" in argv and argv[argv.index("--tools") + 1] == ""
    assert call["cwd_listing"] == []
    assert "the spec" in call["stdin"] and "the plan" in call["stdin"] and '"id": "S1"' in call["stdin"]
    merged = json.loads((tmp_path / "critic" / "critique-merged.json").read_text(encoding="utf-8"))
    assert merged["plan_level"] is True and merged["severity_counts"]["critical"] == 1


def test_mechanical_leak_findings_are_merged_next_to_the_critics_own(tmp_path, fake_env):
    write_json(tmp_path / "scenarios" / "S1.json", {
        "id": "S1", "requirement": "R1", "tier": 2, "surface": {"kind": "doc", "paths": ["d.md"]},
        "task": "Say 603.5.", "expect": {"matches": [r"603\.5"]}})
    proc = _critic(tmp_path, fake_env)
    merged = json.loads((tmp_path / "critic" / "critique-merged.json").read_text(encoding="utf-8"))
    assert proc.returncode == 0
    assert [(f["kind"], f["severity"], f["source"]) for f in merged["findings"]] == [
        ("answer-leak", "critical", "scenario_validate.py")]


def test_a_malformed_scenario_stops_before_any_model_is_paid_for(tmp_path, fake_env):
    write_json(tmp_path / "scenarios" / "S1.json", {"id": "S1", "tier": 9})
    proc = _critic(tmp_path, fake_env)
    assert proc.returncode == 2 and fake_env.invocations() == []


def test_a_crashed_critic_is_an_infrastructure_failure_with_no_merged_file(tmp_path, fake_env):
    _skill_scenario(tmp_path)
    proc = _critic(tmp_path, fake_env, mode="crash")
    assert proc.returncode == 1 and "GATE_RESULT: INFRA_FAILURE" in proc.stdout
    assert not (tmp_path / "critic" / "critique-merged.json").exists()


# --- case_builder.py, on the real incident of agent-ticket-orchestrator#35 --------------------

TICKET = FIXTURES / "incident-122" / "ticket.json"
JUST_BEFORE_TRIAGE = "2026-09-20T18:48:00Z"  # blocked event 18:46:20Z, triage answer 18:48:27Z


def _projects_dir(tmp_path):
    folder = tmp_path / "projects" / "C--store-agent-autonomous-developer-pkg-122-process-ticket-12811e25"
    folder.mkdir(parents=True)
    lines = [
        {"type": "queue-operation", "timestamp": "2026-09-20T18:36:50.000Z", "content": "/process-ticket package=122"},
        {"type": "assistant", "timestamp": "2026-09-20T18:45:00.000Z",
         "message": {"content": [{"type": "text", "text": "PREMISE FALSIFIED — posting blocked."}]}},
        {"type": "assistant", "timestamp": "2026-09-20T19:10:00.000Z",
         "message": {"content": [{"type": "text", "text": "attempt two, after the triage answer"}]}}]
    (folder / "66c6ab00.jsonl").write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")
    return tmp_path / "projects"


def test_blocked_event_of_package_122_is_rebuilt_into_a_triage_input(tmp_path):
    out = tmp_path / "case.md"
    proc = run_script("case_builder.py", "--ticket-file", TICKET, "--at", JUST_BEFORE_TRIAGE,
                      "--step", "triage", "--projects-dir", _projects_dir(tmp_path), "--out", out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "COMMENTS: 4" in proc.stdout and "LATEST_EVENT: blocked" in proc.stdout
    text = out.read_text(encoding="utf-8")
    # the step's input contains the question it had to answer …
    assert "which substitute for per-job data should the check use?" in text
    assert '"event": "blocked"' in text and "PREMISE FALSIFIED — posting blocked." in text
    # … and nothing that only existed afterwards: not the wrong answer, not attempt 2
    assert "## Blocked triage (run)" not in text
    assert "Attempt 2 started" not in text and "attempt two, after the triage answer" not in text


def test_the_unversioned_ticket_body_is_stated_as_a_limit(tmp_path):
    proc = run_script("case_builder.py", "--ticket-file", TICKET, "--at", JUST_BEFORE_TRIAGE,
                      "--step", "triage", "--no-transcript", "--out", tmp_path / "case.md")
    assert proc.returncode == 0
    assert "LIMIT: the ticket was updated at 2026-09-20T22:07:06Z" in proc.stdout
    assert "bodies are not versioned" in (tmp_path / "case.md").read_text(encoding="utf-8")


def test_a_comment_edited_after_the_time_is_flagged(tmp_path):
    payload = json.loads(TICKET.read_text(encoding="utf-8"))
    payload["comments"][0]["updated_at"] = "2026-09-20T21:00:00Z"
    proc = run_script("case_builder.py", "--ticket-file", write_json(tmp_path / "t.json", payload),
                      "--at", JUST_BEFORE_TRIAGE, "--step", "triage", "--no-transcript",
                      "--out", tmp_path / "case.md")
    assert "LIMIT: comment 5751636283 was edited after" in proc.stdout
    assert "EDITED LATER" in (tmp_path / "case.md").read_text(encoding="utf-8")


def test_absent_transcript_folder_is_not_available_and_writes_no_partial_input(tmp_path):
    out = tmp_path / "case.md"
    (tmp_path / "projects").mkdir()
    proc = run_script("case_builder.py", "--ticket-file", TICKET, "--at", JUST_BEFORE_TRIAGE,
                      "--step", "triage", "--projects-dir", tmp_path / "projects", "--out", out)
    assert proc.returncode == 4 and "NOT AVAILABLE" in proc.stdout
    assert not out.exists()


def test_code_state_is_the_commit_the_branch_had_at_the_time(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    for stamp, name in (("2026-09-20T18:00:00Z", "before"), ("2026-09-20T19:00:00Z", "after")):
        (repo / "f.txt").write_text(name, encoding="utf-8")
        git(repo, "add", "-A")
        os.environ["GIT_COMMITTER_DATE"] = os.environ["GIT_AUTHOR_DATE"] = stamp
        try:
            git(repo, "commit", "-q", "-m", name)
        finally:
            del os.environ["GIT_COMMITTER_DATE"], os.environ["GIT_AUTHOR_DATE"]
    proc = run_script("case_builder.py", "--ticket-file", TICKET, "--at", JUST_BEFORE_TRIAGE,
                      "--step", "triage", "--no-transcript", "--repo", repo, "--ref", "main",
                      "--out", tmp_path / "case.md")
    assert proc.returncode == 0, proc.stderr
    code_section = (tmp_path / "case.md").read_text(encoding="utf-8").split("# [case] Code state")[1]
    assert "— before" in code_section and "— after" not in code_section

