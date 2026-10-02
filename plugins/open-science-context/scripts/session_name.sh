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
#                                   on stdin, prints a sessionTitle when the name should change.
#                                   Does nothing when the Claude Code mod is loaded (OPSCI_MOD=1)
#   session_name.sh want [--always] <sid> [dir]
#                                   for the mod: print the name session <sid> should have, or
#                                   nothing when it has it already (--always: print it anyway;
#                                   a resumed session shows its name only once renamed). The
#                                   mod renames the session at once (/rename), not at the next
#                                   prompt
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

# The name session <sid> should have, or nothing when it has it already (ALWAYS=1: print it anyway).
want_name() {  # <sid> <cwd>
  local sid="$1" cwd="$2" f cur="" src="" base want doc task

  f=$(grep -lF "\"sessionId\":\"$sid\"" "$SDIR"/*.json 2>/dev/null | head -n 1)
  if [ -n "$f" ]; then
    cur=$(jq -r '.name // empty' "$f" 2>/dev/null)
    src=$(jq -r '.nameSource // empty' "$f" 2>/dev/null)
  fi

  # Keep the base (and so its number) the session already holds, unless Claude derived it
  # (`proj-3f`) or it is a pane name given by a resurrected batch job (`rr-0-w0p1`).
  base="${cur%%"$SEP"*}"
  if [ -z "$base" ] || [ "$src" = derived ] || [[ "$base" == rr-* ]]; then
    base=$(pick "$(project_of "$cwd")" "$sid")
  fi

  want="$base"
  # This session's own record first (the mod's registration), then whatever this shell's
  # pane or session has registered.
  doc=$(jq -r '.doc_path // empty' "${OPSCI_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/open-science}/session_context/claude__$(printf '%s' "$sid" | tr -c 'A-Za-z0-9._-' '_').json" 2>/dev/null)
  [ -n "$doc" ] && [ -f "$doc" ] || doc=$(bash "$HERE/pane_context.sh" get 2>/dev/null)
  if [ -n "$doc" ]; then
    task=$(task_short "$doc")
    [ -n "$task" ] && want="$base$SEP$task"
  fi

  [ "$want" = "$cur" ] && [ "${ALWAYS:-0}" != 1 ] && return 0
  printf '%s' "$want"
}

cmd_hook() {
  local in sid cwd event want
  in=$(cat) || return 0
  [ "${OPSCI_MOD:-}" = 1 ] && return 0   # the mod renames the session itself
  sid=$(jq -r '.session_id // empty' <<<"$in" 2>/dev/null)
  cwd=$(jq -r '.cwd // empty' <<<"$in" 2>/dev/null)
  event=$(jq -r '.hook_event_name // empty' <<<"$in" 2>/dev/null)
  [ -n "$sid" ] && [ -n "$event" ] || return 0
  want=$(want_name "$sid" "${cwd:-$PWD}")
  [ -n "$want" ] || return 0
  jq -cn --arg e "$event" --arg t "$want" '{hookSpecificOutput: {hookEventName: $e, sessionTitle: $t}}'
}

[ "${OPSCI_SESSION_NAMES:-0}" = 1 ] || { cat >/dev/null 2>&1; exit 0; }
command -v jq >/dev/null 2>&1 || exit 0
case "${1:-}" in
  hook)   cmd_hook ;;
  want)   shift; ALWAYS=0; [ "${1:-}" = --always ] && { ALWAYS=1; shift; }
          [ -n "${1:-}" ] && want_name "$1" "${2:-$PWD}" ;;
  launch) shift; pick "$(project_of "${1:-$PWD}")" ;;
  *) echo "usage: session_name.sh {hook|want <sid> [dir]|launch [dir]}" >&2; exit 2 ;;
esac
exit 0
