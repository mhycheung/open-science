#!/usr/bin/env bash
# Request a session jump: clear this Claude session and (for an active jump) resume
# it from a context file. tmux is required.
#
#   jump.sh active <context file> [--force]   clear, then type
#                                            "/open-science-context:continue-context <context file>"
#   jump.sh wait   <context file>            clear only; a running subagent, background
#                                            shell or SLURM watcher wakes the new session
#   jump.sh cancel                           drop this pane's pending request
#   jump.sh status                           show it
#
# This script only VALIDATES and WRITES a request file. The plugin's Stop hook
# (cm_stop.sh) reads it when the turn ends: for a wait jump it refuses when
# nothing will wake the session; otherwise it starts the detached worker
# (`jump.sh --worker <request>`) that waits for the pane to go idle, types /clear,
# confirms the session id changed, hands the pane registration (pane_context.sh) to
# the new session, and types the resume prompt. The agent never
# types into its own pane, and a jump is detected by this action, never by wording.
#
# Refusals (exit 1): not in tmux; context file missing; context file not modified
# in the last $OPSCI_JUMP_FRESH_MIN minutes (default 15: the state was not saved);
# the session's state file cannot be found (a clear could not be confirmed);
# active jump below $OPSCI_ACTIVE_JUMP_FLOOR tokens (default 100000) without
# --force; an inhibit file exists ($OPSCI_STATE_DIR/inhibit_jump, or
# inhibit_jump_<pane key>; an optional component such as SLURM resurrection may
# create one while it owns the pane).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cm_lib.sh
. "$HERE/cm_lib.sh"

FRESH_MIN="${OPSCI_JUMP_FRESH_MIN:-15}"
FLOOR="${OPSCI_ACTIVE_JUMP_FLOOR:-100000}"
IDLE_TIMEOUT="${OPSCI_JUMP_IDLE_TIMEOUT:-300}"
RECOVER_WINDOW="${OPSCI_JUMP_RECOVER_WINDOW:-1800}"

die() { echo "jump.sh: $*" >&2; exit 1; }

set_phase() {  # <request> <phase>
  local tmp="$1.tmp.$$"
  jq --arg p "$2" --arg at "$(date -Iseconds)" '.phase=$p | .phase_at=$at' "$1" > "$tmp" 2>/dev/null \
    && mv "$tmp" "$1" || rm -f "$tmp"
}

# ------------------------------------------------------------------ worker ----
if [ "${1:-}" = "--worker" ]; then
  REQ="$2"
  [ -f "$REQ" ] || exit 0
  KIND=$(jq -r .kind "$REQ"); SOCK=$(jq -r .sock "$REQ"); PANE=$(jq -r .pane "$REQ")
  SF=$(jq -r .state_file "$REQ"); OLD=$(jq -r .old_sid "$REQ"); PROMPT=$(jq -r '.prompt // ""' "$REQ")
  KEY=$(jq -r .key "$REQ")
  exec 9>"$(cm_lock_path "$KEY")"
  flock -w 120 9 || { cm_log "worker $PANE: pane lock busy for 120 s; jump aborted"; set_phase "$REQ" failed; exit 0; }
  set_phase "$REQ" running
  cm_log "worker $PANE: $KIND jump started (sid ${OLD:0:8})"

  finish() { set_phase "$REQ" "$1"; mkdir -p "$OS_STATE/jump/done"; mv "$REQ" "$OS_STATE/jump/done/$(basename "$REQ" .json)-$(date +%s).json" 2>/dev/null; }

  skip_clear=0
  live=$(cm_sid "$SF")
  if [ -n "$live" ] && [ "$live" != "$OLD" ]; then
    # Someone else already cleared this pane since the request was made.
    [ "$KIND" = wait ] && { cm_log "worker $PANE: already on new sid ${live:0:8}; nothing to do"; cm_reg_handover "$KEY" "$live"; finish done; exit 0; }
    cm_log "worker $PANE: already on new sid ${live:0:8}; skipping the clear"; skip_clear=1
    cm_reg_handover "$KEY" "$live"
  fi

  if [ "$skip_clear" = 0 ]; then
    submitted=0; start=$(date +%s)
    while [ $(( $(date +%s) - start )) -lt "$IDLE_TIMEOUT" ]; do
      cm_wait_idle "$SOCK" "$PANE" "$SF" $(( IDLE_TIMEOUT - ($(date +%s) - start) )) || break
      cm_keys "$SOCK" "$PANE" C-u; sleep 1
      cm_idle_once "$SOCK" "$PANE" "$SF" || { cm_keys "$SOCK" "$PANE" C-u; continue; }
      cm_keys "$SOCK" "$PANE" '/clear'; sleep 2
      # Enter pressed while a turn runs QUEUES the /clear; it would fire later, mid-work.
      cm_idle_once "$SOCK" "$PANE" "$SF" || { cm_log "worker $PANE: went busy after typing /clear; re-waiting"; cm_keys "$SOCK" "$PANE" C-u; continue; }
      [ "$(cm_input_box "$SOCK" "$PANE")" = "/clear" ] || { cm_log "worker $PANE: buffer does not hold exactly /clear; re-waiting"; cm_clear_input "$SOCK" "$PANE"; continue; }
      cm_keys "$SOCK" "$PANE" Enter; submitted=1; break
    done
    if [ "$submitted" = 0 ]; then
      cm_log "worker $PANE: pane never idle for ${IDLE_TIMEOUT} s; nothing typed"
      cm_keys "$SOCK" "$PANE" C-u; finish failed; exit 0
    fi
    new=""; for _ in $(seq 30); do new=$(cm_sid "$SF"); [ -n "$new" ] && [ "$new" != "$OLD" ] && break; sleep 1; done
    if [ -z "$new" ] || [ "$new" = "$OLD" ]; then
      # Fail safe: never type the resume prompt into a session that was not cleared.
      # The TUI may have queued the /clear; if the session id changes later, that is
      # it firing, and the resume prompt is delivered then.
      cm_log "worker $PANE: CLEAR NOT CONFIRMED; watching ${RECOVER_WINDOW} s for a queued clear"
      set_phase "$REQ" unconfirmed
      waited=0
      while [ "$waited" -lt "$RECOVER_WINDOW" ]; do
        new=$(cm_sid "$SF"); [ -n "$new" ] && [ "$new" != "$OLD" ] && break
        sleep 5; waited=$((waited+5))
      done
      if [ -z "$new" ] || [ "$new" = "$OLD" ]; then cm_log "worker $PANE: no clear happened; jump failed"; finish failed; exit 0; fi
      cm_log "worker $PANE: queued clear fired after ${waited} s"
    fi
    cm_log "worker $PANE: cleared, new sid ${new:0:8}"
    cm_reg_handover "$KEY" "$new"
    set_phase "$REQ" cleared
  fi

  if [ "$KIND" = wait ]; then cm_log "worker $PANE: wait jump done; a waker owns the resume"; finish done; exit 0; fi
  sleep 3
  if cm_deliver "$SOCK" "$PANE" "$PROMPT"; then finish done
  else cm_log "worker $PANE: resume prompt NOT delivered; type it by hand: $PROMPT"; finish failed; fi
  exit 0
fi

# ---------------------------------------------------------------- launcher ----
usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
CMD="${1:-}"; shift || true
KEY=$(cm_pane_key) || { [ "$CMD" = status ] && { echo "not in tmux"; exit 0; }; die "not inside tmux: jumps need tmux"; }
REQ=$(cm_request_path "$KEY")

case "$CMD" in
  status)
    if [ -f "$REQ" ]; then jq . "$REQ"; else echo "no jump pending for pane $TMUX_PANE"; fi; exit 0 ;;
  cancel)
    if [ -f "$REQ" ] && [ "$(jq -r .phase "$REQ")" = requested ]; then rm -f "$REQ"; echo "jump request cancelled"
    elif [ -f "$REQ" ]; then echo "a jump is already $(jq -r .phase "$REQ"); it cannot be cancelled" >&2; exit 1
    else echo "no jump pending"; fi
    exit 0 ;;
  active|wait) ;;
  *) usage ;;
esac

CTX="${1:-}"; [ -n "$CTX" ] || usage; shift
FORCE=0; [ "${1:-}" = --force ] && FORCE=1
[ -f "$CTX" ] || die "context file not found: $CTX"
CTX=$(cd "$(dirname "$CTX")" && printf '%s/%s' "$PWD" "$(basename "$CTX")")
case "$CTX" in *[[:space:]]*) die "context file path contains whitespace: $CTX" ;; esac
# The context plugin needs the project management component (plugin open-science-project): the
# context file must sit in a project with AGENTS.md and config/framework.yaml.
d=$(dirname "$CTX")
while [ ! -f "$d/AGENTS.md" ] || [ ! -f "$d/config/framework.yaml" ]; do
  [ "$d" = / ] && die "$CTX is not inside a project made from the open-science template (no AGENTS.md and config/framework.yaml above it). Session jumps need the project management component: create the project with open-science-project:new-project or migrate it with open-science-project:migrate-project."
  d=$(dirname "$d")
done
for f in "$OS_STATE/inhibit_jump" "$OS_STATE/inhibit_jump_$KEY"; do
  [ -e "$f" ] && die "jumps are inhibited by $f: $(head -c 300 "$f" 2>/dev/null)"
done
age_min=$(( ( $(date +%s) - $(stat -c %Y "$CTX") ) / 60 ))
[ "$age_min" -lt "$FRESH_MIN" ] || die "$CTX was last modified ${age_min} min ago. Save the state first (summary, next step, log line), then jump."
CPID=$(cm_claude_pid) || die "no claude process above this shell; cannot confirm a clear"
SF=$(cm_state_file "$CPID") || die "no state file for claude pid $CPID; cannot confirm a clear, refusing"
OLD=$(cm_sid "$SF"); [ -n "$OLD" ] || die "state file $SF has no sessionId"

if [ "$CMD" = active ]; then
  tokens=$(python3 "$HERE/ctx_usage.py" --session-id "$OLD" 2>/dev/null) || tokens=""
  if [ "$FORCE" = 0 ]; then
    [ -n "$tokens" ] || die "cannot read the context size; pass --force to jump anyway"
    [ "$tokens" -ge "$FLOOR" ] || die "context is ${tokens} tokens, below the active-jump floor ${FLOOR}: a jump costs more than it saves. Continue here, or pass --force."
  fi
  PROMPT="/open-science-context:continue-context $CTX"
else
  PROMPT=""
fi

[ -f "$REQ" ] && [ "$(jq -r .phase "$REQ")" != requested ] && die "a jump is already $(jq -r .phase "$REQ") for this pane"
mkdir -p "$(dirname "$REQ")"
jq -n --arg kind "$CMD" --arg ctx "$CTX" --arg prompt "$PROMPT" --arg sock "${TMUX%%,*}" \
      --arg pane "$TMUX_PANE" --arg key "$KEY" --arg sf "$SF" --arg sid "$OLD" --arg at "$(date -Iseconds)" \
  '{version:1, kind:$kind, context:$ctx, prompt:$prompt, sock:$sock, pane:$pane, key:$key,
    state_file:$sf, old_sid:$sid, requested_at:$at, phase:"requested"}' > "$REQ.tmp" && mv "$REQ.tmp" "$REQ" \
  || die "could not write $REQ"
cm_log "launcher $TMUX_PANE: $CMD jump requested (context $CTX)"
# Register the pane, so a session woken after a wait jump finds its file with
# open-science-context:continue-context and no file named.
bash "$HERE/pane_context.sh" set "$CTX" >/dev/null 2>&1 || true

if [ "$CMD" = active ]; then
  echo "Active jump requested. When this turn ends the session is cleared and resumed with:"
  echo "  $PROMPT"
else
  echo "Wait jump requested. When this turn ends the Stop hook checks that something will wake"
  echo "the new session (a background subagent or shell). If nothing will, it refuses and tells you."
fi
echo "Finish anything you owe the user in this turn, then end the turn. Start no new work."
