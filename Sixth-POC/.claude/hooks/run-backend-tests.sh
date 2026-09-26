#!/usr/bin/env bash
# PostToolUse hook: run backend unit tests whenever a file under backend/ is edited/written.
set -euo pipefail

input="$(cat)"
file_path="$(echo "$input" | jq -r '.tool_input.file_path // empty')"

if [[ -z "$file_path" ]]; then
  exit 0
fi

case "$file_path" in
  */backend/*|backend/*)
    ;;
  *)
    exit 0
    ;;
esac

project_dir="$(echo "$input" | jq -r '.cwd // "."')"
cd "$project_dir"

if [[ ! -x venv/bin/pytest ]]; then
  echo "run-backend-tests: venv/bin/pytest not found, skipping" >&2
  exit 0
fi

venv/bin/pytest tests -q
