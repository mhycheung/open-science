#!/usr/bin/env bash
# Stop hook of the open-science plugin (context management). Runs at every stop of
# the main session, reads the hook input JSON on stdin, and:
#
#   1. A pending jump request (written by jump.sh) for this pane:
#        wait jump and nothing will wake the session -> block the stop, cancel the request;
#        otherwise -> kill the cache-cold timer and start the jump worker.
#   2. No jump pending: something will wake the session (a running entry in
#      `background_tasks`, or any `session_crons` entry) -> (re)arm the cache-cold
#      timer; nothing will -> kill it.
#   3. Context above $OPSCI_JUMP_THRESHOLD tokens (default 250000) and no jump
#      pending -> block the stop once with an instruction to do an active jump.
#      After a block, the same session is blocked again only once its context has
#      grown by $OPSCI_JUMP_REPEAT tokens (default 50000), so a session that is
#      waiting for the owner is not stopped at every reply.
#
# Steps 2 and 3 run only for the session that registered this pane with
# pane_context.sh set (the skill does it when it starts driving a task; jump.sh
# does it too, and a jump hands the registration to the new session). Any other
# session, in an unregistered pane or a registered pane it did not register, only
# gets its pane's stale timer killed.
#
# It never reads `last_assistant_message`: jumps are detected by the request file.
# Outside tmux it does nothing. It never fails the stop on its own error.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cm_lib.sh
. "$HERE/cm_lib.sh"

THRESHOLD="${OPSCI_JUMP_THRESHOLD:-250000}"
REPEAT="${OPSCI_JUMP_REPEAT:-50000}"
IN=$(cat)
KEY=$(cm_pane_key) || exit 0
command -v jq >/dev/null 2>&1 || exit 0

block() { jq -n --arg r "$1" '{decision:"block", reason:$r}'; exit 0; }

SID=$(printf '%s' "$IN" | jq -r '.session_id // empty')
ACTIVE=$(printf '%s' "$IN" | jq -r '.stop_hook_active // false')
WAKERS=$(printf '%s' "$IN" | jq -r \
  '([.background_tasks[]? | select((.status // "running") == "running")] | length)
   + ([.session_crons[]?] | length)' 2>/dev/null || echo 0)
REQ=$(cm_request_path "$KEY")

if [ -f "$REQ" ] && [ "$(jq -r .phase "$REQ" 2>/dev/null)" = requested ]; then
  if [ -n "$SID" ] && [ "$(jq -r .old_sid "$REQ")" != "$SID" ]; then
    # Written by an earlier session in this pane that never stopped: stale.
    cm_log "stop $TMUX_PANE: stale jump request from sid $(jq -r .old_sid "$REQ" | cut -c1-8) dropped"
    rm -f "$REQ"
  elif [ "$(jq -r .kind "$REQ")" = wait ] && [ "${WAKERS:-0}" -eq 0 ]; then
    rm -f "$REQ"
    cm_log "stop $TMUX_PANE: wait jump REFUSED, nothing will wake the session"
    block "open-science: wait jump refused and cancelled. Nothing is running that will wake the cleared session (no background subagent, background shell or cron). Either start the waker first (for SLURM jobs: a background Bash running the plugin's wait_slurm.sh <jobid>...) and request the wait jump again, or do an active jump (jump.sh active <context file>)."
  else
    cm_timer_kill "$KEY"
    jq '.phase="launched"' "$REQ" > "$REQ.tmp" && mv "$REQ.tmp" "$REQ"
    setsid nohup bash "$HERE/jump.sh" --worker "$REQ" </dev/null >>"$OS_LOG" 2>&1 &
    cm_log "stop $TMUX_PANE: $(jq -r .kind "$REQ") jump worker started (wakers: ${WAKERS:-0})"
    exit 0
  fi
fi

# A jump already in progress for this pane owns it: touch nothing.
if [ -f "$REQ" ]; then exit 0; fi

# Not opted in: no timer, no size notice.
REG=$(cm_registered_sid "$KEY")
if [ -z "$SID" ] || [ "$REG" != "$SID" ]; then cm_timer_kill "$KEY"; exit 0; fi

if [ "${WAKERS:-0}" -gt 0 ]; then
  cm_timer_kill "$KEY"
  SF=""; CPID=$(cm_claude_pid) && SF=$(cm_state_file "$CPID") || SF=""
  mkdir -p "$OS_STATE/timer"
  setsid nohup bash "$HERE/cache_cold.sh" "${TMUX%%,*}" "$TMUX_PANE" "$KEY" "$SID" "$SF" \
    </dev/null >>"$OS_LOG" 2>&1 &
  echo $! > "$(cm_timer_path "$KEY")"
else
  cm_timer_kill "$KEY"
fi

if [ "$ACTIVE" != true ]; then
  TP=$(printf '%s' "$IN" | jq -r '.transcript_path // empty')
  if [ -n "$TP" ] && command -v python3 >/dev/null 2>&1; then
    tokens=$(python3 "$HERE/ctx_usage.py" --transcript "$TP" 2>/dev/null) || tokens=""
    if [ -n "$tokens" ] && [ "$tokens" -gt "$THRESHOLD" ] 2>/dev/null; then
      NF="$OS_STATE/size/$KEY"   # "<session id> <tokens at the last block>"
      read -r nsid ntok < "$NF" 2>/dev/null || { nsid=""; ntok=0; }
      if [ "$nsid" = "$SID" ] && [ "$tokens" -lt "$(( ${ntok:-0} + REPEAT ))" ] 2>/dev/null; then
        exit 0
      fi
      mkdir -p "$OS_STATE/size" && printf '%s %s\n' "$SID" "$tokens" > "$NF"
      block "open-science: context is ${tokens} tokens, above ${THRESHOLD}. Do an active jump now (skill open-science-context:context-management): save the state to the context file, then run jump.sh active <context file>. If you are about to wait on running work, do a wait jump instead. If this turn ends waiting for the owner (a question, a decision, something only the owner can do), do not jump: say so in one line and stop."
    fi
  fi
fi
exit 0
