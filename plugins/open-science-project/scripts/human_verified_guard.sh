#!/usr/bin/env bash
# PreToolUse hook (Edit|Write|MultiEdit) of the open-science-project plugin. Only the
# project owner sets `verification: human-verified` (AGENTS.md rule 5). This hook
# refuses an edit, inside a template project, whose NEW content sets it: exit 2,
# message on stderr, which Claude Code shows to the agent. Bash commands are not
# checked (a grep for the string must not be refused); the publish check is the
# backstop: it refuses a human-verified node whose `verification:` line was last
# changed in an agent commit.
set -uo pipefail
IN=$(cat)
# Only inside a project made from the open-science template: the nearest directory
# above the file that holds both AGENTS.md and config/framework.yaml.
in_project() {  # <file path>
  local d; d=$(dirname "$1")
  while [ ! -d "$d" ]; do d=$(dirname "$d"); done   # a Write may create the directory
  d=$(cd "$d" 2>/dev/null && pwd) || return 1
  while [ -n "$d" ]; do
    [ -f "$d/AGENTS.md" ] && [ -f "$d/config/framework.yaml" ] && return 0
    [ "$d" = / ] && return 1
    d=$(dirname "$d")
  done
  return 1
}
RE='verification[[:space:]]*:[[:space:]]*["'"'"']?human-verified'
if command -v jq >/dev/null 2>&1; then
    F=$(printf '%s' "$IN" | jq -r '.tool_input.file_path // empty')
    [ -n "$F" ] && in_project "$F" || exit 0
    NEW=$(printf '%s' "$IN" | jq -r '[.tool_input.new_string?, .tool_input.content?,
          (.tool_input.edits[]?.new_string)] | map(select(. != null)) | join("\n")')
else
    NEW=$IN  # no jq: check the whole payload (stricter: an old_string also counts)
fi
if printf '%s' "$NEW" | grep -Eq "$RE"; then
    echo "open-science-project: only the project owner sets 'verification: human-verified' (AGENTS.md rule 5). An agent may set 'verified' with an 'evidence:' pointer. Leave human-verification to the owner, and tell them the result is ready for it." >&2
    exit 2
fi
exit 0
