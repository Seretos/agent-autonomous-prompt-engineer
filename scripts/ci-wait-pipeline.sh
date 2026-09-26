#!/usr/bin/env bash
# CI wait for skills/process-prompt-engineer/SKILL.md Phase 7 (routes its exit codes, incl. 4 = CLI unusable); CLI binary resolution: project-issues skill (agent-project-issues), "Where the binary lives".
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
