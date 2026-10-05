#!/usr/bin/env bash
# Register WHICH context file is being driven in THIS tmux pane, so that
# open-science-context:continue-context / open-science-context:advise-with-context can be invoked
# with no file named and still resolve the right one.
#
# Keyed on the tmux socket and pane id (%N), like the other per-pane records in
# the state directory. The file path is per pane: a fresh session in the same pane
# (after a jump, a resurrection, or a plain /clear) is exactly the case this exists
# to serve, so `get` MUST keep returning it.
#
# The registration is also the opt-in to the plugin's Stop hook (cm_stop.sh): it
# arms the cache-cold timer and gives the context-size notice only for the session
# recorded here as session_id. A jump hands the record to the session it creates
# (jump.sh worker); any other new session in the pane stays unmanaged until it
# runs `set` itself. `clear` turns the hook off for the pane.
#
# The same `set` also records the file under this SESSION (runtime + session id:
# $CLAUDE_CODE_SESSION_ID or the Claude state file, $CODEX_THREAD_ID under Codex), so
# `get` works outside tmux too: it tries the pane record first, then this session's.
# With the Claude Code mod loaded the session record is the registration: the mod hands
# it to the new session at every clear (cm_mod.sh handover), and it survives
# `claude --resume`, so a SLURM resurrection keeps it. Without the mod the session record
# does not follow a jump; in tmux the pane record does, and the Stop hook copies a
# session's record into a new pane that has none (after a resurrection).
#
# Usage:
#   pane_context.sh set <path-to-context.md>        register (overwrites)
#   pane_context.sh get                             print abs path, or exit 1
#   pane_context.sh clear                           drop this pane's and this session's records
#   pane_context.sh status                          human-readable
#   pane_context.sh check                           can this session write the state dir?
#                                                   exit 3 with the fix if not
#
# Under Codex, `set` and `clear` write to the inbox (cm_lib.sh), which the Codex hook takes
# at its next event and turns into the records above; `get` reads this thread's inbox entry
# first. `set` exits 3 with the fix (cm_state_hint) when the sandbox makes the inbox
# read-only; `get` only reads and works either way.
#
# Nothing here may ever block real work: outside tmux, `set` warns and no-ops,
# `get` exits 1 silently, and the callers fall through to asking the user.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cm_lib.sh
. "$HERE/cm_lib.sh"

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

  local rec sid rt srec
  sid=$(cm_live_sid); rt=$(cm_runtime)
  case "$abs" in *[[:space:]]*) echo "pane_context: the path contains whitespace: $abs -- not registering" >&2; return 1 ;; esac
  cm_ctx_ok "$abs" || { echo "pane_context: the path contains a control character -- not registering" >&2; return 1; }
  if [ "$rt" = codex ]; then
    cm_state_writable || { cm_state_hint >&2; return 3; }
    [ -n "$sid" ] || { echo "pane_context: CODEX_THREAD_ID is not set -- not registering" >&2; return 1; }
    cm_write_json "$(cm_inbox_dir "$(cm_inbox_target "$sid")")/context.json" --arg doc "$abs" --arg sid "$sid" \
        --arg at "$(date -Iseconds)" '{version:1, thread_id:$sid, doc_path:$doc, requested_at:$at}' \
      || { cm_state_hint >&2; return 3; }
    echo "pane_context: codex thread $sid now drives $abs (recorded by the Codex hook when this turn ends)"
    return 0
  fi
  if [ -n "$sid" ] && [ "$rt" != none ]; then
    srec=$(cm_sess_reg_path "$rt" "$sid")
    if cm_write_json "$srec" --arg doc "$abs" --arg at "$(date -Iseconds)" --arg sid "$sid" --arg rt "$rt" \
         '{version:1, runtime:$rt, session_id:$sid, doc_path:$doc, registered_at:$at}'; then
      echo "pane_context: $rt session $sid now drives $abs"
    else
      echo "pane_context: cannot write $srec" >&2
    fi
  fi
  if ! rec=$(record_path); then
    cm_mod_active || echo "pane_context: not inside tmux (TMUX_PANE unset) -- no pane registered." >&2
    return 0
  fi
  mkdir -p "$REG_DIR" || { echo "pane_context: cannot write $REG_DIR" >&2; return 1; }
  cm_write_json "$rec" --arg pane "$TMUX_PANE" --arg doc "$abs" \
        --arg at "$(date -Iseconds)" --arg sid "$sid" \
     '{version:1, pane_id:$pane, doc_path:$doc, registered_at:$at, session_id:$sid}' || return 1
  echo "pane_context: pane $TMUX_PANE now drives $abs"
}

# This session's record (outside tmux, or a pane with no record), or return 1.
session_doc() {
  local sid rt f doc
  sid=$(cm_live_sid); rt=$(cm_runtime)
  [ -n "$sid" ] && [ "$rt" != none ] || return 1
  f=$(cm_sess_reg_path "$rt" "$sid")
  doc=$(jq -r '.doc_path // empty' "$f" 2>/dev/null)
  [ -n "$doc" ] && [ -f "$doc" ] || return 1
  printf '%s\n' "$doc"
}

# Under Codex: this thread's registration waiting in the inbox, if any. Prints the file
# (exit 0), or exits 1 for a pending clear or a missing file, or 2 when there is none.
inbox_doc() {
  local sid f doc
  [ "$(cm_runtime)" = codex ] || return 2
  sid=$(cm_live_sid); [ -n "$sid" ] || return 2
  f="$(cm_inbox_dir "$(cm_inbox_target "$sid")")/context.json"
  [ -f "$f" ] && [ "$(jq -r '.thread_id // ""' "$f" 2>/dev/null)" = "$sid" ] || return 2
  doc=$(jq -r '.doc_path // ""' "$f" 2>/dev/null)
  [ -n "$doc" ] && [ -f "$doc" ] || return 1
  printf '%s\n' "$doc"
}

cmd_get() {
  inbox_doc; case $? in 0) return 0 ;; 1) return 1 ;; esac
  local rec; rec=$(record_path) || { session_doc; return; }
  [ -f "$rec" ] || { session_doc; return; }
  local doc; doc=$(jq -r '.doc_path // empty' "$rec" 2>/dev/null)
  [ -n "$doc" ] || { rm -f "$rec" 2>/dev/null; session_doc; return; }
  # A registration pointing at a file that no longer exists is worse than none:
  # drop it so the caller falls through to search-then-ask.
  [ -f "$doc" ] || { rm -f "$rec" 2>/dev/null; session_doc; return; }
  printf '%s\n' "$doc"
}

cmd_clear() {
  local sid rt; sid=$(cm_live_sid); rt=$(cm_runtime)
  if [ "$rt" = codex ]; then
    [ -n "$sid" ] && cm_write_json "$(cm_inbox_dir "$(cm_inbox_target "$sid")")/context.json" --arg sid "$sid" \
        --arg at "$(date -Iseconds)" '{version:1, thread_id:$sid, doc_path:"", requested_at:$at}' \
      || { cm_state_hint >&2; return 3; }
    echo "pane_context: registration of codex thread $sid cleared (by the Codex hook when this turn ends)"
    return 0
  fi
  [ -n "$sid" ] && [ "$rt" != none ] && rm -f "$(cm_sess_reg_path "$rt" "$sid")"
  local rec; rec=$(record_path) || { echo "pane_context: not inside tmux." >&2; return 0; }
  rm -f "$rec"
  echo "pane_context: cleared registration for pane ${TMUX_PANE}"
}

# Exit 0 when this session can write the state directory, else 3 with the fix.
cmd_check() {
  cm_state_writable || { cm_state_hint >&2; return 3; }
  if [ "$(cm_runtime)" = codex ]; then
    echo "pane_context: the inbox $OS_INBOX is writable"
    cm_state_too_open && cm_state_open_hint >&2
  else
    echo "pane_context: $OS_STATE is writable"
  fi
  return 0
}

cmd_status() {
  local sdoc
  if cm_mod_active; then echo "the open-science mod is loaded: the session record is the registration"; fi
  if sdoc=$(session_doc); then echo "$(cm_runtime) session $(cm_live_sid): $sdoc"; fi
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
  check)  cmd_check ;;
  *) echo "usage: pane_context.sh {set <path>|get|clear|status|check}" >&2; exit 2 ;;
esac
