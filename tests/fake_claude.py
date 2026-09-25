"""
A stand-in for the `claude` CLI, started by the scripts under test through
APE_CLAUDE_CMD. It is not a mock of their logic: it is a real child process
that records how it was started (argv, cwd, what the cwd and the --plugin-dir
contained, stdin) and answers in the CLI's real output shapes — stream-json
events or a json result envelope (shapes taken from a live `claude 2.1.278`
run, see tests/fixtures/blind-skill-transcript.jsonl).

What it "knows" comes only from what it was handed, so a baseline artifact and
an after artifact make it answer differently — which is what lets the tests
drive the real baseline-vs-after path end to end.

FAKE_CLAUDE_LOG   directory; one JSON record file per invocation (samples run in
                  parallel, so a shared append-mode file would lose records)
FAKE_CLAUDE_MODE  ok (default) | leak | mcp | crash | crash-one
                  crash-one: exactly one of the parallel processes sharing
                  FAKE_CLAUDE_LOG crashes (the first to claim a lock file
                  there); every other one answers normally — for testing
                  `blind_run.py --retry-invalid` against a partially invalid
                  result without making every sample crash.
FAKE_CLAUDE_CRITIQUE  JSON for structured_output in json mode
FAKE_CLAUDE_FIELDS    JSON object; overrides the `fields` sub-object of a
                      schema-based structured answer (default: every
                      requested field mirrors `could_complete`)
"""
import json
import os
import sys
import time


def arg_after(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv else None


def main():
    argv = sys.argv[1:]
    stdin = sys.stdin.read()
    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    plugin_dir = arg_after(argv, "--plugin-dir")
    system_prompt = arg_after(argv, "--system-prompt")
    output_format = arg_after(argv, "--output-format")
    json_schema_arg = arg_after(argv, "--json-schema")
    log = os.environ.get("FAKE_CLAUDE_LOG")

    if mode == "crash-one":
        # The first process to claim the lock crashes; every other one, in
        # this run or a later --retry-invalid call sharing the same log dir,
        # answers normally. The lock lives NEXT TO the log dir, never inside
        # it — FAKE_CLAUDE_LOG holds one JSON record per invocation, nothing
        # else, and tests read every file in it as JSON.
        lock_dir = (log or ".") + "-locks"
        os.makedirs(lock_dir, exist_ok=True)
        lock_path = os.path.join(lock_dir, "crash-one.lock")
        try:
            os.close(os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            mode = "crash"
        except FileExistsError:
            mode = "ok"

    plugin_files = {}
    if plugin_dir:
        for base, _dirs, files in os.walk(plugin_dir):
            for name in files:
                full = os.path.join(base, name)
                rel = os.path.relpath(full, plugin_dir).replace("\\", "/")
                with open(full, encoding="utf-8") as fh:
                    plugin_files[rel] = fh.read()

    if log:
        os.makedirs(log, exist_ok=True)
        with open(os.path.join(log, f"{time.time_ns()}-{os.getpid()}.json"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"argv": argv, "cwd": os.getcwd(),
                                 "cwd_listing": sorted(os.listdir(".")),
                                 "plugin_files": plugin_files, "stdin": stdin}) + "\n")

    if mode == "crash":
        # A real crash still writes what it managed before dying — an init
        # event — so `last_event_type` is meaningful, not just "no events".
        print(json.dumps({"type": "system", "subtype": "init", "mcp_servers": [], "plugins": []}))
        sys.stderr.write("fake: simulated crash\n")
        return 3

    if output_format == "json":
        critique = json.loads(os.environ.get("FAKE_CLAUDE_CRITIQUE") or
                              '{"findings": [], "solid": [], "unverifiable_without_codebase_access": []}')
        print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                          "structured_output": critique}))
        return 0

    events = [{"type": "system", "subtype": "init",
               "mcp_servers": [{"name": "project-issues"}] if mode == "mcp" else [],
               "plugins": ([{"name": "surface-under-test", "path": plugin_dir}] if plugin_dir else [])
               + [{"name": "agents-md", "path": "builtin"}]}]
    content = []
    if mode == "leak":
        content.append({"type": "tool_use", "name": "Read",
                        "input": {"file_path": os.path.join(os.path.expanduser("~"), "repo", "CLAUDE.md")}})

    structured = None
    if system_prompt is not None:
        # A replayed step: the "agent" escalates only if its instructions say so.
        text = ("STATUS: ESCALATE — no test for model-read prose"
                if "carries no test" in system_prompt
                else "STATUS: ANSWERED — add a pin test on SKILL.md")
    else:
        skill = next((t for rel, t in plugin_files.items() if rel.endswith("SKILL.md")), "")
        source = skill + "\n" + stdin
        if skill and "furlong" in skill.split("---")[1]:
            name = [rel for rel in plugin_files if rel.endswith("SKILL.md")][0].split("/")[-2]
            content.append({"type": "tool_use", "name": "Skill",
                            "input": {"skill": f"surface-under-test:{name}"}})
        knows = "201.168" in source
        text = "3 furlongs are 603.504 meters" if knows else "I do not know the conversion factor"
        structured = {"could_complete": knows, "selected": "unit-docs" if knows else "none",
                      "answer": text, "missing": [] if knows else ["the factor"], "confusing": []}
        if json_schema_arg:
            try:
                schema = json.loads(json_schema_arg)
            except (TypeError, ValueError):
                schema = {}
            field_props = ((schema.get("properties") or {}).get("fields") or {}).get("properties") or {}
            if field_props:
                override = os.environ.get("FAKE_CLAUDE_FIELDS")
                structured["fields"] = (json.loads(override) if override
                                        else {name: knows for name in field_props})
    content.append({"type": "text", "text": text})
    events.append({"type": "assistant", "message": {"role": "assistant", "content": content}})
    events.append({"type": "result", "subtype": "success", "is_error": False, "result": text,
                   "structured_output": structured, "total_cost_usd": 0.0})
    for event in events:
        print(json.dumps(event))
    return 0


if __name__ == "__main__":
    sys.exit(main())
