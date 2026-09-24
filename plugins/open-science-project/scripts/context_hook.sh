#!/usr/bin/env bash
# PostToolUse hook (Edit|Write|MultiEdit) of the open-science-project plugin. After
# an edit to a context file inside a template project it checks the line cap and, for a task context, reminds
# the agent of the four end-of-subtask questions. Caps: `context.md` 200 lines
# (project and task), `map/README.md` 150. Over the cap -> exit 2 with the message
# on stderr, which Claude Code shows to the agent. Any other file, or any file
# outside a template project: silent.
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
command -v jq >/dev/null 2>&1 || exit 0
F=$(printf '%s' "$IN" | jq -r '.tool_input.file_path // empty')
[ -n "$F" ] && [ -f "$F" ] && in_project "$F" || exit 0
base=$(basename "$F"); dir=$(dirname "$F")
parent=$(basename "$dir"); grand=$(basename "$(dirname "$dir")")
if [ "$base" = README.md ] && [ "$parent" = map ]; then cap=150
elif [ "$base" = context.md ]; then cap=200
else exit 0; fi
n=$(wc -l < "$F")
if [ "$n" -gt "$cap" ]; then
  echo "open-science-project: $F has $n lines, over its cap of $cap. Prune it now: move finished or background material to a subcontext/ file or the log, and keep only what the next agent needs." >&2
  exit 2
fi
if [ "$base" = context.md ] && [ "$grand" = tasks ]; then
    msg="open-science-project: task context saved ($n/$cap lines). If a subtask just finished, check: (1) did a status or edge change? update the header; (2) does the next agent need it? update the project context.md; (3) did you use or consult a source or package? update citations/; (4) one line in the task log.md."
    jq -n --arg m "$msg" '{hookSpecificOutput:{hookEventName:"PostToolUse", additionalContext:$m}}'
fi
exit 0
