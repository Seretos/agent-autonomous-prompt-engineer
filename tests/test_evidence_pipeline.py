"""
Behaviour tests for the evidence path: leak check, blind runner (arguments,
isolation, cache, baseline from a git ref), the baseline-vs-after merge, the
scenario-critic runner, and the case builder on the real incident.
"""
import json
import os
import sys

from conftest import FIXTURES, SCRIPTS, git, run_script, write_json

sys.path.insert(0, str(SCRIPTS))

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
        "expect": {"selects": "units", "matches": [r"603\.5"]},
        "controls": [
            {"outcome": "pass", "selected": ["units"],
             "answer_text": "Three furlongs are 603.504 meters."},
            {"outcome": "fail", "selected": ["units"],
             "answer_text": "I'm not sure how long a furlong is."}]})


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
        "task": "Convert something.", "expect": {"selects": "brand-new"},
        "controls": [
            {"outcome": "pass", "selected": ["brand-new"], "answer_text": "42 units."},
            {"outcome": "fail", "selected": [], "answer_text": "I don't know."}]})
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
        "expect": {"matches": ["ESCALATE"]},
        "controls": [
            {"outcome": "pass", "answer_text": "STATUS: ESCALATE — no test for model-read prose"},
            {"outcome": "fail", "answer_text": "STATUS: ANSWERED — add a pin test on SKILL.md"}]})
    proc = _blind(fake_env, scenario, repo, "after", tmp_path / "evidence")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    call = fake_env.invocations()[0]
    argv = call["argv"]
    assert argv[argv.index("--system-prompt") + 1].strip() == "Model-read prose carries no test."
    assert argv[argv.index("--tools") + 1] == "" and "--plugin-dir" not in argv
    assert call["stdin"] == "# [case] the blocked event\n"


def test_a_crashed_sample_records_stderr_tail_and_the_last_event_type(tmp_path, fake_env):
    repo = _repo_with_skill(tmp_path)
    proc = _blind(fake_env, _skill_scenario(tmp_path), repo, "after", tmp_path / "evidence", mode="crash")
    assert proc.returncode == 1
    record = json.loads((tmp_path / "evidence" / "S1.after.json").read_text(encoding="utf-8"))
    for sample in record["samples"]:
        assert not sample["valid"]
        assert "simulated crash" in sample["stderr_tail"]
        assert sample["last_event_type"] == "system"
    assert all("last_event=system" in p["what"]
              for s in record["samples"] for p in s["invalid_reasons"] if p["reason"] == "crashed")


def test_retry_invalid_reruns_only_the_crashed_samples_and_caches_the_completed_result(tmp_path, fake_env):
    repo = _repo_with_skill(tmp_path)
    scenario = _skill_scenario(tmp_path)
    evidence = tmp_path / "evidence"
    first = _blind(fake_env, scenario, repo, "after", evidence, mode="crash-one")
    assert first.returncode == 1
    before = json.loads((evidence / "S1.after.json").read_text(encoding="utf-8"))
    crashed = [s["sample"] for s in before["samples"] if not s["valid"]]
    assert len(crashed) == 1
    calls_after_first = len(fake_env.invocations())

    retry = _blind(fake_env, scenario, repo, "after", evidence, "--retry-invalid", mode="crash-one")
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert "CACHE: retried" in retry.stdout
    assert len(fake_env.invocations()) == calls_after_first + 1  # exactly the one crashed sample reran

    after = json.loads((evidence / "S1.after.json").read_text(encoding="utf-8"))
    assert all(s["valid"] for s in after["samples"])
    # the two samples that were already valid are untouched
    kept = {s["sample"]: s["answer_text"] for s in after["samples"] if s["sample"] != crashed[0]}
    assert kept == {s["sample"]: s["answer_text"] for s in before["samples"] if s["sample"] != crashed[0]}

    third = _blind(fake_env, scenario, repo, "after", tmp_path / "evidence2", mode="crash-one")
    assert "CACHE: hit" in third.stdout  # ticket #3: a completed retry is cached like any other run


def test_no_cache_only_skips_the_read_a_valid_run_is_still_cached(tmp_path, fake_env):
    repo = _repo_with_skill(tmp_path)
    scenario = _skill_scenario(tmp_path)
    evidence = tmp_path / "evidence"
    proc = _blind(fake_env, scenario, repo, "after", evidence, "--no-cache")
    assert proc.returncode == 0 and "CACHE: miss" in proc.stdout
    again = _blind(fake_env, scenario, repo, "after", tmp_path / "evidence2")
    assert "CACHE: hit" in again.stdout


def test_fields_are_added_to_the_consumer_schema_and_scored_end_to_end(tmp_path, fake_env):
    repo = _repo_with_skill(tmp_path)
    scenario = write_json(tmp_path / "scenarios" / "S1.json", {
        "id": "S1", "requirement": "R1", "tier": 2,
        "surface": {"kind": "skill", "paths": ["skills/units/SKILL.md"]},
        "task": "Is a furlong longer than 200 meters?",
        "fields": {"over_200m": {"type": "boolean", "question": "Is a furlong over 200 meters?"}},
        "expect": {"selects": "units", "fields": {"over_200m": True}},
        "controls": [
            {"outcome": "pass", "selected": ["units"], "answer_text": "Yes.",
             "structured": {"fields": {"over_200m": True}}},
            {"outcome": "fail", "selected": ["units"], "answer_text": "No.",
             "structured": {"fields": {"over_200m": False}}}]})
    evidence = tmp_path / "evidence"
    baseline = _blind(fake_env, scenario, repo, "baseline", evidence, "--git-ref", "main")
    assert baseline.returncode == 0
    call = fake_env.invocations()[0]
    schema = json.loads(call["argv"][call["argv"].index("--json-schema") + 1])
    assert "fields" in schema["properties"]
    assert schema["properties"]["fields"]["properties"]["over_200m"]["type"] == "boolean"

    after = _blind(fake_env, scenario, repo, "after", evidence)
    assert after.returncode == 0
    proc = run_script("evidence_merge.py", "--scenarios-dir", tmp_path / "scenarios",
                      "--results-dir", evidence, "--out", tmp_path / "merged.json")
    row = json.loads((tmp_path / "merged.json").read_text(encoding="utf-8"))["scenarios"][0]
    # old text doesn't know the factor (over_200m defaults false==knows); new text does
    assert row["baseline"]["passes"] == 0 and row["after"]["passes"] == 3
    assert row["verdict"] == "improved"


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
        "task": "decide", "expect": {"matches": ["ESCALATE"], "forbids": ["pin test"]},
        "controls": [
            {"outcome": "pass", "answer_text": "STATUS: ESCALATE"},
            {"outcome": "fail", "answer_text": "STATUS: ANSWERED add a pin test"}]})
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
    # the failing sample also fails `matches` (not only `forbids`), so this is
    # a real content finding, never `suspect`
    assert not merged["scenarios"][0].get("suspect")
    assert not merged["findings"][0]["suspect"]


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


# --- evidence_merge.py: suspect / repair (agent-autonomous-prompt-engineer#5) ---------------


def _suspect_case(tmp_path, baseline_answers, after_answers, repairs=None):
    """A scenario whose only `expect` is a `forbids` regex on "by itself" —
    the real shape of agent-project-issues#386's attempt-2/S1: every right
    answer that states the (correct) negative necessarily echoes the task's
    own words back, so a right answer fails on `forbids` alone, never on
    `matches`/`selects`/`fields`."""
    write_json(tmp_path / "scenarios" / "S1.json", {
        "id": "S1", "requirement": "R1", "tier": 2, "surface": {"kind": "doc", "paths": ["d.md"]},
        "task": "Does this call by itself merge the PR?",
        "expect": {"forbids": [r"by itself"]}})

    def samples(texts):
        return [{"sample": i + 1, "valid": True, "selected": [], "structured": None, "answer_text": t}
                for i, t in enumerate(texts)]
    write_json(tmp_path / "results" / "S1.baseline.json", {"samples": samples(baseline_answers), "cache": "hit"})
    write_json(tmp_path / "results" / "S1.after.json", {"samples": samples(after_answers)})
    args = ["evidence_merge.py", "--scenarios-dir", tmp_path / "scenarios", "--results-dir",
            tmp_path / "results", "--out", tmp_path / "merged.json"]
    if repairs is not None:
        args += ["--repairs-file", write_json(tmp_path / "repairs.json", repairs)]
    proc = run_script(*args)
    return proc, json.loads((tmp_path / "merged.json").read_text(encoding="utf-8"))


ECHOES_THE_TASK = ["No, not by itself — Bob still has to approve.",
                   "No, that call by itself does not merge it.",
                   "No — by itself it only requests review."]


def test_a_scenario_that_rejects_every_right_answer_is_suspect_and_triggers_repair(tmp_path):
    proc, merged = _suspect_case(tmp_path, baseline_answers=ECHOES_THE_TASK, after_answers=ECHOES_THE_TASK)
    assert proc.returncode == 4 and merged["result"] == "repair"
    assert merged["scenarios"][0]["verdict"] == "unchanged" and merged["scenarios"][0]["suspect"]
    assert merged["repairable"] == ["S1"]
    assert merged["findings"] == []  # pulled out, not reported as a prose finding
    assert "REPAIR: S1" in proc.stdout


def test_a_missing_repairs_file_is_read_as_nothing_repaired_yet(tmp_path):
    proc, merged = _suspect_case(tmp_path, baseline_answers=ECHOES_THE_TASK, after_answers=ECHOES_THE_TASK,
                                 repairs=None)
    # (repairs=None means the flag is never passed — the file simply does not exist)
    assert proc.returncode == 4 and merged["repairable"] == ["S1"]


def test_a_suspect_scenario_already_named_in_repairs_file_is_a_real_finding_once(tmp_path):
    proc, merged = _suspect_case(tmp_path, baseline_answers=ECHOES_THE_TASK, after_answers=ECHOES_THE_TASK,
                                 repairs=["S1"])
    assert proc.returncode == 1 and merged["result"] == "fail"
    assert merged["repairable"] == []
    assert merged["findings"][0]["suspect"] is True
    assert merged["scenarios"][0]["repaired"] is True
    assert "REPAIR_USED: S1" in proc.stdout


def test_a_regression_can_also_be_suspect(tmp_path):
    proc, merged = _suspect_case(tmp_path, baseline_answers=["Yes.", "Yes.", "No, not by itself."],
                                 after_answers=ECHOES_THE_TASK)
    assert proc.returncode == 4  # baseline 2/3 (the "Yes." answers don't contain "by itself")
    assert merged["scenarios"][0]["verdict"] == "regressed" and merged["scenarios"][0]["suspect"]


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
    # the package states what the blind consumer actually has, from the same
    # constants blind_run.py starts it with — not a second, hand-written copy
    import blind_run  # noqa: E402 (scripts/ was added to sys.path above)
    for kind, tools in blind_run.TOOLS_BY_KIND.items():
        assert kind in call["stdin"]
        if tools:
            assert ", ".join(tools) in call["stdin"]
    assert "no MCP" in call["stdin"] or "no MCP servers" in call["stdin"]
    merged = json.loads((tmp_path / "critic" / "critique-merged.json").read_text(encoding="utf-8"))
    assert merged["plan_level"] is True and merged["severity_counts"]["critical"] == 1


def test_mechanical_leak_findings_are_merged_next_to_the_critics_own(tmp_path, fake_env):
    write_json(tmp_path / "scenarios" / "S1.json", {
        "id": "S1", "requirement": "R1", "tier": 2, "surface": {"kind": "doc", "paths": ["d.md"]},
        "task": "Say 603.5.", "expect": {"matches": [r"603\.5"]},
        "controls": [
            {"outcome": "pass", "answer_text": "It's 603.5 meters."},
            {"outcome": "fail", "answer_text": "I don't know."}]})
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


# --- rundir_archive.py (agent-autonomous-prompt-engineer#5: a failed run's <rundir> used to
#     be deleted with the worktree, unrecoverably — see tests/fixtures/incident-386/README.md) --


def test_rundir_archive_copies_the_run_dir_outside_the_worktree(tmp_path):
    rundir = tmp_path / "worktree" / ".ape" / "386-1"
    (rundir / "scenarios").mkdir(parents=True)
    (rundir / "scenarios" / "S1.json").write_text("{}", encoding="utf-8")
    (rundir / "plan.md").write_text("the plan\n", encoding="utf-8")
    archive_root = tmp_path / "archive"

    proc = run_script("rundir_archive.py", "--rundir", rundir, "--project", "agent-project-issues",
                      "--package", "386", "--attempt", "1", env={"APE_ARCHIVE_DIR": str(archive_root)})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ARCHIVE: " in proc.stdout
    archived = next(archive_root.iterdir())
    assert archived.name.startswith("agent-project-issues-386-1-")
    assert (archived / "plan.md").read_text(encoding="utf-8") == "the plan\n"
    assert (archived / "scenarios" / "S1.json").exists()
    # the original is untouched — this is a copy, not a move
    assert (rundir / "plan.md").exists()


def test_rundir_archive_refuses_a_target_inside_a_git_checkout(tmp_path):
    rundir = tmp_path / "worktree" / ".ape" / "386-1"
    rundir.mkdir(parents=True)
    repo = tmp_path / "some-repo"
    (repo / ".git").mkdir(parents=True)

    proc = run_script("rundir_archive.py", "--rundir", rundir, "--project", "p", "--package", "1",
                      "--attempt", "1", env={"APE_ARCHIVE_DIR": str(repo / "ape-runs")})
    assert proc.returncode == 1
    assert "inside a git checkout" in proc.stderr


def test_rundir_archive_reports_a_missing_rundir_without_crashing(tmp_path):
    proc = run_script("rundir_archive.py", "--rundir", tmp_path / "nope", "--project", "p",
                      "--package", "1", "--attempt", "1",
                      env={"APE_ARCHIVE_DIR": str(tmp_path / "archive")})
    assert proc.returncode == 1 and "not a directory" in proc.stderr

