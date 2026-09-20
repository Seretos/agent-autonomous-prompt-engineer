"""
Behaviour tests for the small model-free scripts: event block, tier selector,
tier-0 lint, scenario validation, stagnation check.
"""
import json

from conftest import REPO, git, run_script, write_json

# --- event_block.py -------------------------------------------------------------------------


def test_event_block_counts_rounds_and_renders_the_callers_key_value_block(tmp_path):
    rounds = tmp_path / "rounds.json"
    assert "CAP: open" in run_script("event_block.py", "bump", rounds, "review", "f").stdout
    run_script("event_block.py", "bump", rounds, "review", "i")
    third = run_script("event_block.py", "bump", rounds, "review", "f")
    assert "USED: 3/3" in third.stdout and "CAP: soft" in third.stdout

    out = run_script("event_block.py", "render", "--event", "review-verdict", "--package", "7",
                     "--attempt", "2", "--rounds-file", rounds, "--pr", "12").stdout
    lines = out.strip().splitlines()
    assert lines[0] == "<!-- adev:event v1" and lines[-1] == "-->"
    # The caller parses dumb `key: value` lines; every contract key must be one.
    parsed = dict(line.split(":", 1) for line in lines[1:-1])
    assert {k: v.strip() for k, v in parsed.items()} == {
        "event": "review-verdict", "package": "7", "attempt": "2", "generation": "1/2",
        "rounds": "scenario-critic=0/3(0f,0i) evidence=0/3(0f,0i) review=3/3(2f,1i) "
                  "ci=0/3(0f,0i) rebase=0/3(0f,0i)",
        "pr": "12", "ci_run": ""}


def test_event_block_reaches_the_hard_cap_and_refuses_unknown_names(tmp_path):
    rounds = tmp_path / "rounds.json"
    for _ in range(3):
        last = run_script("event_block.py", "bump", rounds, "ci", "i")
    assert "CAP: hard" in last.stdout  # ci: soft == hard == 3

    bad_event = run_script("event_block.py", "render", "--event", "evidence-verdict",
                           "--package", "1", "--rounds-file", rounds)
    assert bad_event.returncode == 2 and "closed" in bad_event.stderr and bad_event.stdout == ""
    assert run_script("event_block.py", "bump", rounds, "test-critic", "f").returncode == 2


# --- tier_select.py -------------------------------------------------------------------------


def _requirements(tmp_path, *reqs):
    return write_json(tmp_path / "requirements.json", {"requirements": list(reqs)})


def test_tier_comes_from_kind_and_paths(tmp_path):
    reqs = _requirements(
        tmp_path,
        {"id": "R1", "kind": "prose-step", "paths": ["agents/triage.md"]},
        {"id": "R2", "kind": "prose-surface", "paths": ["skills/run/SKILL.md"]},
        {"id": "R3", "kind": "prose-other", "paths": ["AGENTS.md"]})
    proc = run_script("tier_select.py", "--requirements", reqs)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert {r["id"]: r["tiers"] for r in result["requirements"]} == {"R1": [0, 1], "R2": [0, 2], "R3": [0]}
    assert result["tier0_files"] == ["AGENTS.md", "agents/triage.md", "skills/run/SKILL.md"]
    assert result["foreign_requirements"] == []


def test_script_requirement_is_reported_as_foreign_not_tiered(tmp_path):
    reqs = _requirements(
        tmp_path,
        {"id": "R1", "kind": "prose-other", "paths": ["skills/run/SKILL.md"]},
        {"id": "R2", "kind": "script", "paths": ["scripts/ci-promised-check.py"]})
    result = json.loads(run_script("tier_select.py", "--requirements", reqs).stdout)
    assert result["foreign_requirements"] == ["R2"]
    assert result["violations"] == []


def test_prose_declared_requirement_naming_code_is_a_violation(tmp_path):
    reqs = _requirements(tmp_path, {"id": "R1", "kind": "prose-other",
                                    "paths": ["skills/run/SKILL.md", "scripts/gate.py"]})
    proc = run_script("tier_select.py", "--requirements", reqs)
    assert proc.returncode == 1
    violations = json.loads(proc.stdout)["violations"]
    assert [(v["code"], v["path"]) for v in violations] == [("prose-declared-touches-code", "scripts/gate.py")]


def test_step_kind_without_an_agent_or_skill_file_is_a_violation(tmp_path):
    reqs = _requirements(tmp_path, {"id": "R1", "kind": "prose-step", "paths": ["docs/howto.md"]})
    proc = run_script("tier_select.py", "--requirements", reqs)
    assert proc.returncode == 1
    assert json.loads(proc.stdout)["violations"][0]["code"] == "step-kind-without-step-file"


def test_real_diff_touching_code_is_caught_even_though_the_declaration_was_clean(tmp_path):
    """The declaration names only prose; the writer nevertheless edited a
    workflow. Only the git-derived form of the call can see that."""
    repo = tmp_path / "repo"
    (repo / "agents").mkdir(parents=True)
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / "agents" / "triage.md").write_text("old\n", encoding="utf-8")
    (repo / ".github" / "workflows" / "lint.yml").write_text("on: push\n", encoding="utf-8")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "pkg/1")
    (repo / "agents" / "triage.md").write_text("new\n", encoding="utf-8")
    (repo / ".github" / "workflows" / "lint.yml").write_text("on: pull_request\n", encoding="utf-8")
    (repo / ".ape").mkdir()
    (repo / ".ape" / "plan.md").write_text("scratch\n", encoding="utf-8")

    reqs = _requirements(tmp_path, {"id": "R1", "kind": "prose-other", "paths": ["agents/triage.md"]})
    proc = run_script("tier_select.py", "--requirements", reqs, "--worktree", repo, "--base", "main")
    result = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert [(v["code"], v["path"]) for v in result["violations"]] == [
        ("prose-declared-touches-code", ".github/workflows/lint.yml")]
    # Scratch under .ape/ is never part of the package's diff.
    assert result["tier0_files"] == ["agents/triage.md"]


def test_unknown_kind_is_rejected(tmp_path):
    reqs = _requirements(tmp_path, {"id": "R1", "kind": "driving-test", "paths": ["agents/a.md"]})
    proc = run_script("tier_select.py", "--requirements", reqs)
    assert proc.returncode == 1 and json.loads(proc.stdout)["violations"][0]["code"] == "unknown-kind"


# --- lint_prose.py --------------------------------------------------------------------------

GOOD_AGENT = "---\nname: triage\ndescription: Answers blocked events: from the ticket.\nmodel: sonnet\n---\nBody.\n"


def _plugin(tmp_path):
    root = tmp_path / "plugin"
    (root / "agents").mkdir(parents=True)
    (root / "scripts").mkdir()
    return root


def test_lint_accepts_a_sound_agent_file(tmp_path):
    root = _plugin(tmp_path)
    (root / "agents" / "triage.md").write_bytes(GOOD_AGENT.encode())
    proc = run_script("lint_prose.py", "--root", root, "agents/triage.md")
    assert proc.returncode == 0, proc.stdout
    assert "TIER0: OK" in proc.stdout


def test_lint_finds_crlf_which_the_harness_would_silently_ignore(tmp_path):
    root = _plugin(tmp_path)
    (root / "agents" / "triage.md").write_bytes(GOOD_AGENT.replace("\n", "\r\n").encode())
    proc = run_script("lint_prose.py", "--root", root, "agents/triage.md")
    assert proc.returncode == 1 and "[crlf]" in proc.stdout


def test_lint_finds_broken_frontmatter_and_a_name_that_does_not_match_the_file(tmp_path):
    root = _plugin(tmp_path)
    (root / "agents" / "triage.md").write_bytes(b"---\nname: triager\n---\nBody.\n")
    (root / "agents" / "bare.md").write_bytes(b"# no frontmatter\n")
    proc = run_script("lint_prose.py", "--root", root, "--all", "--out", tmp_path / "t0.json")
    assert proc.returncode == 1
    findings = json.loads((tmp_path / "t0.json").read_text(encoding="utf-8"))["findings"]
    whats = {(f["file"], f["what"]) for f in findings}
    assert ("agents/triage.md", "missing or empty `description`") in whats
    assert ("agents/triage.md", "`name: triager` does not match 'triage'") in whats
    assert any(f["file"] == "agents/bare.md" and f["code"] == "frontmatter" for f in findings)


def test_lint_finds_a_referenced_script_that_does_not_exist(tmp_path):
    root = _plugin(tmp_path)
    (root / "scripts" / "here.py").write_text("print(1)\n", encoding="utf-8")
    body = GOOD_AGENT + 'Run `python "${CLAUDE_PLUGIN_ROOT}/scripts/here.py"` then ' \
                        '`${CLAUDE_PLUGIN_ROOT}/scripts/gone.py`.\n'
    (root / "agents" / "triage.md").write_bytes(body.encode())
    proc = run_script("lint_prose.py", "--root", root, "agents/triage.md")
    assert proc.returncode == 1
    assert "scripts/gone.py does not exist" in proc.stdout and "here.py does not" not in proc.stdout


CONTRACT_SCRIPT = ('import json, sys\n'
                   'if sys.argv[1:] == ["--print-contract", "kinds"]:\n'
                   '    print(json.dumps(["alpha", "beta"]))\n')


def _agent_with_table(rows):
    table = "\n".join(f"| `{r}` | x |" for r in rows)
    return (GOOD_AGENT + "<!-- ape:contract script=scripts/kinds.py name=kinds -->\n"
            "| kind | meaning |\n|---|---|\n" + table + "\n<!-- /ape:contract -->\n")


def test_lint_contract_table_that_agrees_with_its_script_passes(tmp_path):
    root = _plugin(tmp_path)
    (root / "scripts" / "kinds.py").write_text(CONTRACT_SCRIPT, encoding="utf-8")
    (root / "agents" / "triage.md").write_bytes(_agent_with_table(["alpha", "beta"]).encode())
    assert run_script("lint_prose.py", "--root", root, "agents/triage.md").returncode == 0


def test_lint_contract_table_that_drifted_from_its_script_fails_both_ways(tmp_path):
    root = _plugin(tmp_path)
    (root / "scripts" / "kinds.py").write_text(CONTRACT_SCRIPT, encoding="utf-8")
    (root / "agents" / "triage.md").write_bytes(_agent_with_table(["alpha", "gamma"]).encode())
    proc = run_script("lint_prose.py", "--root", root, "agents/triage.md")
    assert proc.returncode == 1
    assert "missing ['beta']" in proc.stdout and "not in script ['gamma']" in proc.stdout


def test_this_plugins_own_prose_passes_its_own_tier_0():
    """The one check of this repo's skill and agents: mechanical only —
    frontmatter the harness parses, LF, referenced files, contract tables
    against the scripts. No wording is asserted."""
    proc = run_script("lint_prose.py", "--root", REPO, "--all")
    assert proc.returncode == 0, proc.stdout


# --- scenario_validate.py -------------------------------------------------------------------


def _scenario(tmp_path, **overrides):
    scenario = {"id": "S1", "requirement": "R1", "tier": 2,
                "surface": {"kind": "skill", "paths": ["skills/units/SKILL.md"]},
                "task": "How many meters are three of those old horse-racing lengths?",
                "expect": {"selects": "units", "matches": [r"603\.5"]}}
    scenario.update(overrides)
    return write_json(tmp_path / "scenarios" / f"{scenario['id']}.json", scenario)


def test_honest_scenario_validates(tmp_path):
    proc = run_script("scenario_validate.py", _scenario(tmp_path))
    assert proc.returncode == 0 and "SCENARIOS: OK" in proc.stdout


def test_task_that_contains_its_own_expected_answer_is_an_answer_leak(tmp_path):
    path = _scenario(tmp_path, task="Confirm that 3 furlongs are 603.5 meters.")
    proc = run_script("scenario_validate.py", path)
    assert proc.returncode == 1 and "[answer-leak]" in proc.stdout


def test_task_that_names_the_surface_it_should_discover_is_an_answer_leak(tmp_path):
    path = _scenario(tmp_path, task="Use the units skill to convert 3 furlongs.")
    proc = run_script("scenario_validate.py", path)
    assert proc.returncode == 1 and "names 'units'" in proc.stdout


def test_scenario_without_any_expectation_is_unfalsifiable(tmp_path):
    proc = run_script("scenario_validate.py", _scenario(tmp_path, expect={}))
    assert proc.returncode == 1 and "[unfalsifiable]" in proc.stdout


def test_step_replay_needs_a_real_case_and_the_recorded_wrong_verdict(tmp_path):
    path = _scenario(tmp_path, tier=1, surface={"kind": "agent-step", "paths": ["agents/triage.md"]},
                     case_input="cases/missing.case.md")
    proc = run_script("scenario_validate.py", path)
    assert proc.returncode == 1
    assert "recorded_wrong" in proc.stdout and "does not exist" in proc.stdout


# --- stagnation_check.py --------------------------------------------------------------------


def test_new_finding_is_progress_and_a_repeat_is_stagnation(tmp_path):
    history = tmp_path / "history.json"
    first = write_json(tmp_path / "r1.json", {"findings": [
        {"kind": "tailored", "severity": "critical", "violated_criterion": "S1"}]})
    second = write_json(tmp_path / "r2.json", {"findings": [
        {"kind": "tailored", "severity": "critical", "violated_criterion": "S1",
         "what": "reworded, same finding"},
        {"kind": "answer-leak", "severity": "minor", "violated_criterion": "S2"}]})
    assert "RESULT: progress" in run_script("stagnation_check.py", "scenario-critic", first, history).stdout
    repeat = run_script("stagnation_check.py", "scenario-critic", second, history).stdout
    # The reworded `what` and the new *minor* finding do not buy another round.
    assert "RESULT: stagnation" in repeat and "RECURRING: tailored | S1" in repeat


def test_review_fingerprint_is_kind_file_and_the_start_of_what(tmp_path):
    history = tmp_path / "history.json"
    finding = {"kind": "contradiction", "severity": "blocking", "file": "agents/a.md",
               "what": "the old rule in Protocol step 3 still stands next to the new one"}
    first = write_json(tmp_path / "r1.json", {"findings": [finding]})
    other_file = write_json(tmp_path / "r2.json", {"findings": [dict(finding, file="agents/b.md")]})
    nit_only = write_json(tmp_path / "r3.json", {"findings": [dict(finding, severity="nit")]})
    assert "progress" in run_script("stagnation_check.py", "review", first, history).stdout
    assert "stagnation" in run_script("stagnation_check.py", "review", first, history).stdout
    assert "progress" in run_script("stagnation_check.py", "review", other_file, history).stdout
    assert "RESULT: clean" in run_script("stagnation_check.py", "review", nit_only, history).stdout


def test_stagnation_check_never_defaults_a_verdict_on_bad_input(tmp_path):
    proc = run_script("stagnation_check.py", "review", tmp_path / "absent.json", tmp_path / "h.json")
    assert proc.returncode == 2 and "RESULT" not in proc.stdout
    assert run_script("stagnation_check.py", "plan-critic", tmp_path / "x", tmp_path / "h").returncode == 2
