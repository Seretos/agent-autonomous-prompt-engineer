#!/usr/bin/env bash
# CI wait for process-prompt-engineer Phase 7 (copied from agent-autonomous-developer): resolve the project-issues CLI by
# EXECUTING a candidate (on Windows / Git Bash the bare name can resolve to a
# Linux ELF next to project-issues.exe -> rc 126/127), then pass the CLI's
# stdout and exit code through unchanged. If no candidate is executable, exit 4
# (the documented "CLI unusable" lane -> list_pipeline_runs fallback).
# Usage: ci-wait-pipeline.sh --project <id> --sha <sha> --timeout <s>
set -u

case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*) candidates="project-issues.exe project-issues" ;;
  *)                    candidates="project-issues project-issues.exe" ;;
esac

tried=""
for c in $candidates; do
  "$c" wait-pipeline "$@"
  rc=$?
  if [ "$rc" -ne 126 ] && [ "$rc" -ne 127 ]; then
    exit "$rc"
  fi
  tried="$tried $c(rc=$rc)"
done

echo "ci-wait-pipeline: no usable project-issues CLI (tried:$tried)" >&2
exit 4
