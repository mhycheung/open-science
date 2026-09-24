#!/usr/bin/env bash
# Register WHICH context file is being driven in THIS tmux pane, so that
# open-science-context:continue-context / open-science-context:advise-with-context can be invoked
# with no file named and still resolve the right one.
#
# Keyed on the tmux socket and pane id (%N), like the other per-pane records in
# the state directory. The Claude session id is recorded too, but only for
# debugging -- a fresh session in the same pane (after a jump, a resurrection, or
# a plain /clear) is exactly the case this exists to serve, so it MUST inherit the
# registration.
#
# Usage:
#   pane_context.sh set <path-to-context.md>        register (overwrites)
#   pane_context.sh get                             print abs path, or exit 1
#   pane_context.sh clear                           drop this pane's record
#   pane_context.sh status                          human-readable
#
# Nothing here may ever block real work: outside tmux, `set` warns and no-ops,
# `get` exits 1 silently, and the callers fall through to asking the user.
set -uo pipefail

STATE_DIR="${OPSCI_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/open-science}"
REG_DIR="$STATE_DIR/pane_context"

pane_key() { printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'; }

record_path() {
  [ -n "${TMUX_PANE:-}" ] && [ -n "${TMUX:-}" ] || return 1
  printf '%s/%s__%s.json' "$REG_DIR" "$(pane_key "${TMUX%%,*}")" "$(pane_key "$TMUX_PANE")"
}

cmd_set() {
  local raw="${1:-}"
  [ -n "$raw" ] || { echo "usage: pane_context.sh set <path>" >&2; return 2; }
  if [ ! -f "$raw" ]; then
    echo "pane_context: no such file: $raw -- not registering" >&2
    return 1
  fi
  local abs; abs=$(cd "$(dirname "$raw")" && printf '%s/%s' "$PWD" "$(basename "$raw")")

  local rec
  if ! rec=$(record_path); then
    echo "pane_context: not inside tmux (TMUX_PANE unset) -- nothing registered." >&2
    return 0
  fi
  mkdir -p "$REG_DIR" || return 1
  jq -n --arg pane "$TMUX_PANE" --arg doc "$abs" \
        --arg at "$(date -Iseconds)" --arg sid "${CLAUDE_CODE_SESSION_ID:-}" \
     '{version:1, pane_id:$pane, doc_path:$doc, registered_at:$at, session_id:$sid}' \
     > "$rec.tmp" && mv "$rec.tmp" "$rec" || return 1
  echo "pane_context: pane $TMUX_PANE now drives $abs"
}

cmd_get() {
  local rec; rec=$(record_path) || return 1
  [ -f "$rec" ] || return 1
  local doc; doc=$(jq -r '.doc_path // empty' "$rec" 2>/dev/null)
  [ -n "$doc" ] || { rm -f "$rec"; return 1; }
  # A registration pointing at a file that no longer exists is worse than none:
  # drop it so the caller falls through to search-then-ask.
  [ -f "$doc" ] || { rm -f "$rec"; return 1; }
  printf '%s\n' "$doc"
}

cmd_clear() {
  local rec; rec=$(record_path) || { echo "pane_context: not inside tmux." >&2; return 0; }
  rm -f "$rec"
  echo "pane_context: cleared registration for pane ${TMUX_PANE}"
}

cmd_status() {
  if [ -z "${TMUX_PANE:-}" ]; then echo "not inside tmux -- no pane registration possible"; return 0; fi
  local rec; rec=$(record_path)
  if [ ! -f "$rec" ]; then echo "pane $TMUX_PANE: no context doc registered"; return 0; fi
  local doc; doc=$(jq -r '.doc_path // empty' "$rec")
  local at;  at=$(jq -r '.registered_at // "unrecorded"' "$rec")
  local sid; sid=$(jq -r '.session_id // "unrecorded"' "$rec")
  echo "pane $TMUX_PANE"
  echo "  doc:        $doc$([ -f "$doc" ] || printf ' (MISSING -- will be cleared on next get)')"
  echo "  registered: $at"
  echo "  by session: $sid"
}

case "${1:-}" in
  set)    shift; cmd_set "${1:-}" ;;
  get)    cmd_get ;;
  clear)  cmd_clear ;;
  status) cmd_status ;;
  *) echo "usage: pane_context.sh {set <path>|get|clear|status}" >&2; exit 2 ;;
esac
