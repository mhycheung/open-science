#!/usr/bin/env bash
# Codex hook of the open-science context plugin: SessionStart, UserPromptSubmit and Stop.
# Claude Code does not use it (its hooks are cm_stop.sh and session_name.sh).
#
# Codex runs hooks as children of its TUI, outside the sandbox, with the TUI's TMUX and
# TMUX_PANE (measured, codex-cli 0.159.3). Each event:
#
#   every event        write codex/threads/<thread>.json and, in tmux, codex/panes/<key>.json:
#                      status (busy after UserPromptSubmit, idle after SessionStart and Stop),
#                      TUI pid, pane, model, rollout path. Nothing else can tell which Codex
#                      thread runs in which pane.
#   SessionStart       a Codex jump that relaunched this pane: hand the pane's context
#                      registration to the new thread and mark the jump done.
#   Stop               1. a pending jump request for this pane (jump.sh): a wait jump with no
#                         waker is refused (block, request cancelled); otherwise the jump
#                         worker starts. 2. queued wakers (wait_slurm.sh --notify) start.
#                      3. the registered session's context is above the threshold: block once
#                         with an instruction to jump (as cm_stop.sh does for Claude).
#
# A Codex Stop "block" makes Codex continue with the reason as a new prompt; that is the
# same effect as Claude's. There is no cache-cold timer for Codex: no prompt-cache lifetime
# is documented for it. Outside tmux only the thread record and wakers are handled.
# It never fails the hook on its own error.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cm_lib.sh
. "$HERE/cm_lib.sh"
command -v jq >/dev/null 2>&1 || { cat >/dev/null; exit 0; }

IN=$(cat)
EVENT=$(jq -r '.hook_event_name // empty' <<<"$IN" 2>/dev/null)
SID=$(jq -r '.session_id // empty' <<<"$IN" 2>/dev/null)
[ -n "$EVENT" ] && [ -n "$SID" ] || exit 0
MODEL=$(jq -r '.model // empty' <<<"$IN")
TP=$(jq -r '.transcript_path // empty' <<<"$IN")
CWD=$(jq -r '.cwd // empty' <<<"$IN")
KEY=$(cm_pane_key) || KEY=""
T="${TMUX:-}"; SOCKP="${T%%,*}"
# The TUI and its start time (cm_proc_start), so a reused pid is never taken for it. A hook
# that cannot see the TUI keeps what the thread's record already says.
if CPID=$(cm_codex_pid "$PPID"); then CSTART=$(cm_proc_start "$CPID")
else
  CPID=$(jq -r '.tui_pid // ""' "$(cm_cx_thread_path "$SID")" 2>/dev/null)
  CSTART=$(jq -r '.tui_start // ""' "$(cm_cx_thread_path "$SID")" 2>/dev/null)
fi
case "$EVENT" in UserPromptSubmit) STATUS=busy ;; *) STATUS=idle ;; esac

block() { jq -n --arg r "$1" '{decision:"block", reason:$r}'; exit 0; }

# ---- records ----------------------------------------------------------------------
rec_args=(--arg tid "$SID" --arg st "$STATUS" --arg ev "$EVENT" --arg pid "$CPID" --arg pst "$CSTART"
          --arg sock "$SOCKP" --arg pane "${TMUX_PANE:-}" --arg key "$KEY"
          --arg model "$MODEL" --arg tp "$TP" --arg cwd "$CWD" --arg home "${CODEX_HOME:-}"
          --arg src "$(jq -r '.source // empty' <<<"$IN")" --arg at "$(date -Iseconds)" --arg ep "$(date +%s)")
REC='{version:1, runtime:"codex", thread_id:$tid, status:$st, event:$ev, tui_pid:$pid, tui_start:$pst,
      sock:$sock, pane:$pane, key:$key, model:$model, transcript_path:$tp, cwd:$cwd,
      codex_home:$home, source:$src, updated_at:$at, updated_epoch:($ep|tonumber)}'
cm_write_json "$(cm_cx_thread_path "$SID")" "${rec_args[@]}" "$REC" || true
if [ -n "$KEY" ]; then
  [ -n "${TMUX:-}" ] && cm_write_json "$(cm_cx_pane_path "$KEY")" "${rec_args[@]}" "$REC" || true
fi

MODE=$(cm_jump_mode)
REQ=""; [ -n "$KEY" ] && REQ=$(cm_request_path "$KEY")

# ---- SessionStart: finish a relaunch --------------------------------------------------
if [ "$EVENT" = SessionStart ]; then
  if [ -n "$REQ" ] && [ -f "$REQ" ] && [ "$(jq -r '.runtime // ""' "$REQ")" = codex ] \
     && [ "$(jq -r .phase "$REQ")" = relaunched ] && [ "$(jq -r .old_sid "$REQ")" != "$SID" ]; then
    cm_reg_handover "$KEY" "$SID"
    cm_log "cx SessionStart $TMUX_PANE: relaunched as thread ${SID:0:8}; registration handed over"
    tmp="$REQ.tmp.$$"
    jq --arg s "$SID" --arg at "$(date -Iseconds)" '.new_sid=$s | .phase="done" | .phase_at=$at' "$REQ" > "$tmp" \
      && mkdir -p "$OS_STATE/jump/done" && mv "$tmp" "$OS_STATE/jump/done/$KEY-$(date +%s).json" && rm -f "$REQ"
  fi
  exit 0
fi
[ "$EVENT" = Stop ] || exit 0

# ---- Stop -------------------------------------------------------------------------------
# Wakers queued by wait_slurm.sh --notify for this pane (or, outside tmux, this thread).
WTARGET="${KEY:-thread__$(cm_key "$SID")}"
# A waker counts while it is queued, while it runs (its pid alive with the recorded start
# time), and for 60 s after it was claimed but before it recorded its pid.
live_wakers() {  # count queued + running wakers for this target
  local f n=0 st pid pst at
  for f in "$OS_STATE/wakers/$WTARGET"/*.json; do
    [ -f "$f" ] || continue
    read -r st pid pst at < <(jq -r '[.state // "-", .pid // "-", .pid_start // "-", .started_epoch // 0] | map(tostring) | join(" ")' "$f")
    case "$st" in
      requested) n=$((n+1)) ;;
      running)
        if [ "$pid" != - ]; then
          kill -0 "$pid" 2>/dev/null && { [ "$pst" = - ] || [ "$(cm_proc_start "$pid")" = "$pst" ]; } && n=$((n+1))
        elif [ $(( $(date +%s) - at )) -lt 60 ]; then n=$((n+1)); fi ;;
    esac
  done
  echo "$n"
}
# Claim each queued waker (requested -> running, under the record's lock) BEFORE starting
# it: the waker then only ever adds its pid and finishes, so it can never be overwritten
# by this hook, and two overlapping Stop hooks cannot start the same waker twice.
start_wakers() {
  local f
  for f in "$OS_STATE/wakers/$WTARGET"/*.json; do
    [ -f "$f" ] || continue
    cm_json_update "$f" 'if .state == "requested" then .state="running" | .started_epoch=($e|tonumber) else error("taken") end' \
      --arg e "$(date +%s)" || continue
    setsid nohup bash "$HERE/wait_slurm.sh" --run-waker "$f" </dev/null >>"$OS_LOG" 2>&1 &
    cm_log "cx stop: waker $(basename "$f") started (pid $!)"
  done
}
[ "$MODE" = off ] || start_wakers
[ -n "$KEY" ] || exit 0
[ "$MODE" = off ] && exit 0

if [ -f "$REQ" ] && [ "$(jq -r .phase "$REQ" 2>/dev/null)" = requested ]; then
  if [ "$(jq -r '.runtime // "claude"' "$REQ")" != codex ] || [ "$(jq -r .old_sid "$REQ")" != "$SID" ]; then
    cm_log "cx stop $TMUX_PANE: stale jump request from sid $(jq -r .old_sid "$REQ" | cut -c1-8) dropped"
    rm -f "$REQ"
  elif [ "$(jq -r .kind "$REQ")" = wait ] && [ "$(live_wakers)" -eq 0 ]; then
    rm -f "$REQ"
    cm_log "cx stop $TMUX_PANE: wait jump REFUSED, no waker"
    block "open-science: wait jump refused and cancelled. Nothing will wake the new Codex session: no waker is queued or running for this pane. Start one first (for SLURM jobs: bash <plugin>/scripts/wait_slurm.sh --notify <jobid>...) and request the wait jump again, or do an active jump (jump.sh active <context file>)."
  else
    tmp="$REQ.tmp.$$"; jq '.phase="launched"' "$REQ" > "$tmp" && mv "$tmp" "$REQ"
    setsid nohup bash "$HERE/jump.sh" --worker "$REQ" </dev/null >>"$OS_LOG" 2>&1 &
    cm_log "cx stop $TMUX_PANE: codex $(jq -r .kind "$REQ") jump worker started"
    exit 0
  fi
fi
[ -f "$REQ" ] && exit 0

# Size notice, for the session that registered this pane only.
[ "$(cm_registered_sid "$KEY")" = "$SID" ] || exit 0
[ "$MODE" = all ] || exit 0
[ "$(jq -r '.stop_hook_active // false' <<<"$IN")" = true ] && exit 0
[ -n "$TP" ] && command -v python3 >/dev/null 2>&1 || exit 0
u=$(python3 "$HERE/ctx_usage.py" --codex-rollout "$TP" --json 2>/dev/null) || exit 0
tokens=$(jq -r '.tokens // empty' <<<"$u"); window=$(jq -r '.window // empty' <<<"$u")
[ -n "$tokens" ] || exit 0
# Default threshold: 60% of the model's window (Codex compacts on its own near the end).
if [ -n "${OPSCI_CODEX_JUMP_THRESHOLD:-}" ]; then TH="$OPSCI_CODEX_JUMP_THRESHOLD"
elif [ -n "$window" ] && [ "$window" -gt 0 ] 2>/dev/null; then TH=$(( window * ${OPSCI_CODEX_JUMP_PCT:-60} / 100 ))
else exit 0; fi
[ "$tokens" -gt "$TH" ] 2>/dev/null || exit 0
REPEAT="${OPSCI_JUMP_REPEAT:-50000}"
NF="$OS_STATE/size/$KEY"
read -r nsid ntok < "$NF" 2>/dev/null || { nsid=""; ntok=0; }
if [ "$nsid" = "$SID" ] && [ "$tokens" -lt "$(( ${ntok:-0} + REPEAT ))" ] 2>/dev/null; then exit 0; fi
mkdir -p "$OS_STATE/size" && printf '%s %s\n' "$SID" "$tokens" > "$NF"
block "open-science: context is ${tokens} tokens, above ${TH}. Do an active jump now (skill open-science-context:context-management): save the state to the context file, then run jump.sh active <context file>; this Codex session then ends and a fresh one starts in this pane from the context file. If you are about to wait on running work, do a wait jump instead. If this turn ends waiting for the user, do not jump: say so in one line and stop."
