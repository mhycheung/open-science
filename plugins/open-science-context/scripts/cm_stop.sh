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
#      waiting for the user is not stopped at every reply.
#
# Steps 2 and 3 run only for the session that registered this pane with
# pane_context.sh set (the skill does it when it starts driving a task; jump.sh
# does it too, and a jump hands the registration to the new session). Any other
# session, in an unregistered pane or a registered pane it did not register, only
# gets its pane's stale timer killed.
#
# $OPSCI_JUMPS (the user's choice in onboarding) limits this: `off` makes the hook do
# nothing but kill a stale timer; `wait` skips step 3. Unset or `all`: all three steps.
#
# It never reads `last_assistant_message`: jumps are detected by the request file.
# Outside tmux it does nothing. It never fails the stop on its own error.
#
# With the Claude Code mod loaded (OPSCI_MOD=1) the plain hook does nothing: the mod runs
# `cm_stop.sh --mod` at each stop instead, with the hook JSON plus `opsci_tokens` (the
# context size the mod reads from Claude Code) on stdin. The same three steps then run on
# records keyed by session id (cm_sess_key), the registration is this session's record
# (pane_context.sh set), queued wakers (wait_slurm.sh --notify) count as wakers, and
# nothing is started: the answer is one JSON object for the mod,
#   {"decision":"block","reason":...}   (optional) block the stop with this text
#   "opsci": {"jump": {kind, context, prompt, report, wakers}}   clear now; write the report
#                                                at the top of the new session; for an active
#                                                jump, run prompt
#   "opsci": {"cold": <seconds>, "notice": <text>}   (re)arm the cache-cold timer; 0 kills it
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cm_lib.sh
. "$HERE/cm_lib.sh"

THRESHOLD="${OPSCI_JUMP_THRESHOLD:-250000}"
REPEAT="${OPSCI_JUMP_REPEAT:-50000}"
MOD=0; [ "${1:-}" = --mod ] && MOD=1
# The mod runs this policy itself (--mod); the plain Stop hook then has nothing to do.
[ "$MOD" = 0 ] && [ "${OPSCI_MOD:-}" = 1 ] && { cat >/dev/null; exit 0; }
IN=$(cat)
command -v jq >/dev/null 2>&1 || exit 0
MODE=$(cm_jump_mode)

wait_refused="open-science: wait jump refused and cancelled. Nothing is running that will wake the cleared session (no background subagent, background shell or cron). Either start the waker first (for SLURM jobs: the plugin's wait_slurm.sh, as open-science-context:context-management says) and request the wait jump again, or do an active jump (jump.sh active <context file> --report \"<report>\")."
size_notice() { printf 'open-science: context is %s tokens, above %s. Do an active jump now (skill open-science-context:context-management): save the state to the context file, then run jump.sh active <context file> --report \"<report for the user>\". If you are about to wait on running work, do a wait jump instead. If this turn ends waiting for the user (a question, a decision, something only the user can do), do not jump: say so in one line and stop.' "$1" "$THRESHOLD"; }

# Has the size notice already been given to this session at about this size? Records it if not.
size_due() {  # <key> <sid> <tokens>
  local NF="$OS_STATE/size/$1" nsid ntok
  read -r nsid ntok < "$NF" 2>/dev/null || { nsid=""; ntok=0; }
  if [ "$nsid" = "$2" ] && [ "$3" -lt "$(( ${ntok:-0} + REPEAT ))" ] 2>/dev/null; then return 1; fi
  printf '%s %s\n' "$2" "$3" | cm_write_text "$NF"
}

SID=$(printf '%s' "$IN" | jq -r '.session_id // empty')
ACTIVE=$(printf '%s' "$IN" | jq -r '.stop_hook_active // false')
WAKERS=$(printf '%s' "$IN" | jq -r \
  '([.background_tasks[]? | select((.status // "running") == "running")] | length)
   + ([.session_crons[]?] | length)' 2>/dev/null || echo 0)

# ---- the mod's policy: records keyed by session, actions returned as JSON ----------
if [ "$MOD" = 1 ]; then
  out() {  # [block reason]; uses JUMP and COLD
    jq -n --arg r "${1:-}" --argjson jump "${JUMP:-null}" --argjson cold "${COLD:-0}" \
          --arg notice "$(cm_cold_notice "$(cm_cold_seconds)")" \
      '(if $r != "" then {decision:"block", reason:$r} else {} end)
       + {opsci: ({cold:$cold, notice:$notice} + (if $jump then {jump:$jump} else {} end))}'
    exit 0
  }
  JUMP=null; COLD=0
  [ -n "$SID" ] || out
  [ "$MODE" = off ] && out
  KEY=$(cm_sess_key "$SID")
  REQ=$(cm_request_path "$KEY")
  QUEUED=$(ls "$OS_STATE/wakers/$KEY"/*.json 2>/dev/null | wc -l)
  WAKERS=$(( ${WAKERS:-0} + QUEUED ))
  if [ -f "$REQ" ] && [ "$(jq -r .phase "$REQ" 2>/dev/null)" = requested ]; then
    RKIND=$(jq -r '.kind // ""' "$REQ" 2>/dev/null); RCTX=$(jq -r '.context // ""' "$REQ" 2>/dev/null)
    if [ "$(jq -r .old_sid "$REQ")" != "$SID" ]; then
      cm_log "stop mod ${SID:0:8}: stale jump request from sid $(jq -r .old_sid "$REQ" | cut -c1-8) dropped"
      rm -f "$REQ"
    elif ! cm_plain_file "$REQ" || { [ "$RKIND" != active ] && [ "$RKIND" != wait ]; } \
         || { [ "$RKIND" = active ] && ! cm_ctx_ok "$RCTX"; }; then
      cm_log "stop mod ${SID:0:8}: jump request refused (not a plain file, kind '$RKIND', or context not a valid file path)"
      rm -f "$REQ"
    elif [ "$(jq -r .kind "$REQ")" = wait ] && [ "$WAKERS" -eq 0 ]; then
      rm -f "$REQ"
      cm_log "stop mod ${SID:0:8}: wait jump REFUSED, nothing will wake the session"
      out "$wait_refused"
    else
      # No prompt: the mod builds the resume command from the context file itself.
      JUMP=$(jq -c --argjson w "$WAKERS" '{kind, context, report: ((.report // "") | tostring), wakers: $w}' "$REQ")
      cm_jq_into "$OS_STATE/jump/done/$KEY-$(date +%s).json" '.phase="handed_to_mod"' "$REQ" && rm -f "$REQ"
      cm_log "stop mod ${SID:0:8}: $(jq -r .kind <<<"$JUMP") jump handed to the mod (wakers: $WAKERS)"
      out
    fi
  fi
  # Opted in by this session's registration (pane_context.sh set); a jump hands it over.
  [ -f "$(cm_sess_reg_path claude "$SID")" ] || out
  [ "$WAKERS" -gt 0 ] && COLD=$(cm_cold_seconds)
  TOK=$(printf '%s' "$IN" | jq -r '.opsci_tokens // empty')
  if [ "$ACTIVE" != true ] && [ "$MODE" = all ] && [ -n "$TOK" ] && [ "$TOK" -gt "$THRESHOLD" ] 2>/dev/null \
     && size_due "$KEY" "$SID" "$TOK"; then
    out "$(size_notice "$TOK")"
  fi
  out
fi

# ---- the tmux path ----------------------------------------------------------------
KEY=$(cm_pane_key) || exit 0
# Jumps off: no request can exist (jump.sh refuses), no timer, no size notice.
[ "$MODE" = off ] && { cm_timer_kill "$KEY"; exit 0; }

block() { jq -n --arg r "$1" '{decision:"block", reason:$r}'; exit 0; }

REQ=$(cm_request_path "$KEY")

if [ -f "$REQ" ] && [ "$(jq -r .phase "$REQ" 2>/dev/null)" = requested ]; then
  if [ -n "$SID" ] && [ "$(jq -r .old_sid "$REQ")" != "$SID" ]; then
    # Written by an earlier session in this pane that never stopped: stale.
    cm_log "stop $TMUX_PANE: stale jump request from sid $(jq -r .old_sid "$REQ" | cut -c1-8) dropped"
    rm -f "$REQ"
  elif [ "$(jq -r .kind "$REQ")" = wait ] && [ "${WAKERS:-0}" -eq 0 ]; then
    rm -f "$REQ"
    cm_log "stop $TMUX_PANE: wait jump REFUSED, nothing will wake the session"
    block "$wait_refused"
  else
    cm_timer_kill "$KEY"
    cm_jq_into "$REQ" '.phase="launched"' "$REQ"
    setsid nohup bash "$HERE/jump.sh" --worker "$REQ" </dev/null >>"$OS_LOG" 2>&1 &
    cm_log "stop $TMUX_PANE: $(jq -r .kind "$REQ") jump worker started (wakers: ${WAKERS:-0})"
    exit 0
  fi
fi

# A jump already in progress for this pane owns it: touch nothing.
if [ -f "$REQ" ]; then exit 0; fi

# Not opted in: no timer, no size notice. A pane with no record whose session has one
# (registered elsewhere, or resumed in a new pane after a SLURM resurrection) adopts it.
REG=$(cm_registered_sid "$KEY")
if [ -z "$REG" ] && [ -n "$SID" ] && [ ! -f "$(cm_reg_path "$KEY")" ]; then
  doc=$(jq -r '.doc_path // empty' "$(cm_sess_reg_path claude "$SID")" 2>/dev/null)
  if [ -n "$doc" ] && [ -f "$doc" ]; then
    cm_write_json "$(cm_reg_path "$KEY")" --arg pane "${TMUX_PANE:-}" --arg doc "$doc" \
      --arg at "$(date -Iseconds)" --arg sid "$SID" \
      '{version:1, pane_id:$pane, doc_path:$doc, registered_at:$at, session_id:$sid}' && REG="$SID"
    cm_log "stop $TMUX_PANE: adopted session ${SID:0:8}'s registration ($doc)"
  fi
fi
if [ -z "$SID" ] || [ "$REG" != "$SID" ]; then cm_timer_kill "$KEY"; exit 0; fi

if [ "${WAKERS:-0}" -gt 0 ]; then
  cm_timer_kill "$KEY"
  SF=""; CPID=$(cm_claude_pid) && SF=$(cm_state_file "$CPID") || SF=""
  setsid nohup bash "$HERE/cache_cold.sh" "${TMUX%%,*}" "$TMUX_PANE" "$KEY" "$SID" "$SF" \
    </dev/null >>"$OS_LOG" 2>&1 &
  echo $! | cm_write_text "$(cm_timer_path "$KEY")"
else
  cm_timer_kill "$KEY"
fi

if [ "$ACTIVE" != true ] && [ "$MODE" = all ]; then
  TP=$(printf '%s' "$IN" | jq -r '.transcript_path // empty')
  if [ -n "$TP" ] && command -v python3 >/dev/null 2>&1; then
    tokens=$(python3 "$HERE/ctx_usage.py" --transcript "$TP" 2>/dev/null) || tokens=""
    if [ -n "$tokens" ] && [ "$tokens" -gt "$THRESHOLD" ] 2>/dev/null; then
      size_due "$KEY" "$SID" "$tokens" && block "$(size_notice "$tokens")"
    fi
  fi
fi
exit 0
