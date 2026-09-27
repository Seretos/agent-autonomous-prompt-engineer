#!/usr/bin/env bash
# Builds the marketplace-dispatch JSON payload with jq instead of a heredoc,
# so a `"` or `\` in DESC or in the release notes can never break the JSON
# the way the old `PAYLOAD=$(cat <<EOF ... EOF)` heredoc did (#14). Reads
# NAME/DESC/REPO/VERSION/TAG from the environment; the notes body is read
# from a file (never interpolated as a shell string) so a multi-line notes
# body round-trips byte for byte, trailing newline included.
#
# Usage: release_payload.sh <notes-file>
# Prints the dispatch JSON on stdout, exit 0. Exits nonzero (jq's/bash's own
# code) if `jq` is missing, the notes file cannot be read, or a required env
# var is unset with `set -u` active.
set -euo pipefail

NOTES_FILE="$1"

ICON_URL="https://raw.githubusercontent.com/${REPO}/${TAG}/assets/icon.png"
DESCRIPTION_URL="https://raw.githubusercontent.com/${REPO}/${TAG}/description.md"

jq -n \
  --arg name "$NAME" \
  --arg description "$DESC" \
  --arg repo "$REPO" \
  --arg version "$VERSION" \
  --arg ref "$TAG" \
  --arg icon "$ICON_URL" \
  --arg description_url "$DESCRIPTION_URL" \
  --rawfile notes "$NOTES_FILE" \
  '{
    event_type: "plugin-release",
    client_payload: {
      name: $name,
      description: $description,
      repo: $repo,
      category: "skill",
      version: $version,
      ref: $ref,
      icon: $icon,
      description_url: $description_url,
      notes: $notes
    }
  }'
