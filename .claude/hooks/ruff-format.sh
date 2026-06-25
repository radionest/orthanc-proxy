#!/usr/bin/env bash
# PostToolUse hook: lint-fix + format an edited Python file with ruff.
# Reads the tool-call JSON on stdin; acts only when file_path is a *.py file.
# Never blocks the edit — every step is best-effort and the hook always exits 0.
set -uo pipefail

file=$(jq -r '.tool_input.file_path // empty' 2>/dev/null || true)
case "$file" in
  *.py) ;;
  *) exit 0 ;;
esac
[ -f "$file" ] || exit 0

uvx ruff check --fix "$file" >/dev/null 2>&1 || true
uvx ruff format "$file" >/dev/null 2>&1 || true
exit 0
