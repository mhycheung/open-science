#!/usr/bin/env bash
# Session names that say which project and task a session is on, for the Remote Control list
# in the Claude app and on claude.ai, the prompt box and the list of peer sessions:
#
#   <project>                 the first live session in a project
#   <project>-2, -3, ...      further live sessions in the same project
#   <project>-2 · <task>      once this tmux pane has a registered context file
#
# Off unless OPSCI_SESSION_NAMES=1 (the "env" block of the Claude settings.json; onboarding
# asks). <project> is the basename of the git top level (else the cwd), lower case, anything
# but [a-z0-9] turned into '-'. <task> is the `short_name:` in the registered context file's
# header, else the task id for tasks/<id>/, else the directory name with a leading
# YYYY-MM-DD- removed; a project context.md adds no task.
#
# Usage:
#   session_name.sh hook            SessionStart / UserPromptSubmit hook: reads the hook JSON
#                                   on stdin, prints a sessionTitle when the name should change
#   session_name.sh launch [dir]    print a free name, for a launch wrapper that sets
#                                   CLAUDE_CODE_SESSION_NAME (optional)
#
# Only a UserPromptSubmit sessionTitle renames the session; a SessionStart one sets the title
# in the prompt box only. So a new registration shows at the next prompt.
#
# Names are per account: only sessions of this config dir ($CLAUDE_CONFIG_DIR, else
# ~/.claude) count. A session is live when its pid exists and /proc/<pid>/stat's start time
# equals the recorded procStart. Never blocks a session: every failure prints nothing, exit 0.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEP=' · '
SDIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/sessions"

slug() { printf '%s' "$1" | tr 'A-Z' 'a-z' | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//'; }

project_of() {  # <dir>
  local top s
  top=$(git -C "$1" rev-parse --show-toplevel 2>/dev/null) || top="$1"
  s=$(slug "$(basename "$top")")
  printf '%s' "${s:-session}"
}

# The part before SEP of every live session's name, one per line, leaving out session $1.
live_bases() {
  local self="${1:-}" f rec pid ps sid name
  for f in "$SDIR"/*.json; do
    [ -f "$f" ] || continue
    rec=$(jq -r '[.pid, .procStart // "", .sessionId // "", .name // ""] | @tsv' "$f" 2>/dev/null) || continue
    IFS=$'\t' read -r pid ps sid name <<<"$rec"
    [ -n "$name" ] && [ "$sid" != "$self" ] || continue
    [ -r "/proc/$pid/stat" ] || continue
    [ "$(awk '{print $22}' "/proc/$pid/stat" 2>/dev/null)" = "$ps" ] || continue
    printf '%s\n' "${name%%"$SEP"*}"
  done
}

pick() {  # <project> [own session id] -> the first of project, project-2, ... not taken
  local base="$1" taken n=2 cand="$1"
  taken=$(live_bases "${2:-}")
  while grep -qxF -- "$cand" <<<"$taken"; do cand="$base-$n"; n=$((n + 1)); done
  printf '%s' "$cand"
}

task_short() {  # <context file> -> the task's short name; nothing for a project context.md
  local doc="$1" v dir
  v=$(head -n 40 "$doc" | sed -nE 's/^(short_name|Short name):[[:space:]]*"?([^"]*)"?[[:space:]]*$/\2/p' | head -n 1)
  if [ -n "$v" ]; then printf '%s' "$v"; return; fi
  dir=$(basename "$(dirname "$doc")")
  if [ "$(basename "$(dirname "$(dirname "$doc")")")" = tasks ]; then printf '%s' "$dir"; return; fi
  [ -d "$(dirname "$doc")/tasks" ] && return
  printf '%s' "$dir" | sed -E 's/^[0-9]{4}-[0-9]{2}-[0-9]{2}-//'
}

cmd_hook() {
  local in sid cwd event f cur="" src="" base want doc task
  in=$(cat) || return 0
  sid=$(jq -r '.session_id // empty' <<<"$in" 2>/dev/null)
  cwd=$(jq -r '.cwd // empty' <<<"$in" 2>/dev/null)
  event=$(jq -r '.hook_event_name // empty' <<<"$in" 2>/dev/null)
  [ -n "$sid" ] && [ -n "$event" ] || return 0
  [ -n "$cwd" ] || cwd=$PWD

  f=$(grep -lF "\"sessionId\":\"$sid\"" "$SDIR"/*.json 2>/dev/null | head -n 1)
  if [ -n "$f" ]; then
    cur=$(jq -r '.name // empty' "$f" 2>/dev/null)
    src=$(jq -r '.nameSource // empty' "$f" 2>/dev/null)
  fi

  # Keep the base (and so its number) the session already holds, unless Claude derived it
  # (`proj-3f`) or it is a slurm-resurrect pane name (`rr-0-w0p1`).
  base="${cur%%"$SEP"*}"
  if [ -z "$base" ] || [ "$src" = derived ] || [[ "$base" == rr-* ]]; then
    base=$(pick "$(project_of "$cwd")" "$sid")
  fi

  want="$base"
  if doc=$(bash "$HERE/pane_context.sh" get 2>/dev/null) && [ -n "$doc" ]; then
    task=$(task_short "$doc")
    [ -n "$task" ] && want="$base$SEP$task"
  fi

  [ "$want" = "$cur" ] && return 0
  jq -cn --arg e "$event" --arg t "$want" '{hookSpecificOutput: {hookEventName: $e, sessionTitle: $t}}'
}

[ "${OPSCI_SESSION_NAMES:-0}" = 1 ] || { cat >/dev/null 2>&1; exit 0; }
command -v jq >/dev/null 2>&1 || exit 0
case "${1:-}" in
  hook)   cmd_hook ;;
  launch) shift; pick "$(project_of "${1:-$PWD}")" ;;
  *) echo "usage: session_name.sh {hook|launch [dir]}" >&2; exit 2 ;;
esac
exit 0
