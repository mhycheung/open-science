#!/usr/bin/env bash
# SLURM waker for a wait jump.
#
# Claude Code with the open-science mod loaded, and Codex: queue a waker,
#
#   wait_slurm.sh --notify <jobid> [<jobid>...]
#
# which only writes a request (<state>/wakers/<key>/...json) and returns. Under Claude Code
# the mod polls it (`wait_slurm.sh --check <request>`, every $OPSCI_WAIT_POLL seconds) and,
# when the jobs have left the queue, sends their states to the session, which starts a
# turn. The request is keyed by session id, so it survives a jump (the mod moves it to the
# new session) and a SLURM resurrection (the resumed session keeps its id). Codex: below.
#
# Claude Code WITHOUT the mod (`--notify` says so and exits 4): run it as a BACKGROUND Bash
# task (run_in_background):
#
#   wait_slurm.sh <jobid> [<jobid>...]
#
# It polls `squeue` every $OPSCI_WAIT_POLL seconds (default 60) and exits when none
# of the jobs is queued or running, printing each job's final state from `sacct`.
# Claude Code lists the running task in the Stop hook's `background_tasks`, so the
# cache-cold timer and the wait-jump check see it, and its exit wakes the session.
# Exit 0 when every job ended COMPLETED, 1 when any did not, 2 on a usage error.
#
# Codex: a background command's exit does not wake a Codex session, and a Codex jump
# ends the process that ran it, so it queues a waker too (`--notify`), keyed by pane or
# thread. The Codex Stop
# hook (cx_hook.sh) starts it detached, outside the sandbox, as
# `wait_slurm.sh --run-waker <request>`: it polls the same way, then sends the job states
# with `codex queue` to the Codex thread running in the pane AT THAT TIME (after a jump,
# the new one: it first waits for a jump in flight in the pane to finish), or outside tmux
# to the thread that queued it.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLL="${OPSCI_WAIT_POLL:-60}"

check_ids() {
  [ $# -ge 1 ] || { echo "usage: wait_slurm.sh [--notify] <jobid> [<jobid>...]" >&2; exit 2; }
  for j in "$@"; do [[ "$j" =~ ^[0-9]+(_[0-9]+)?$ ]] || { echo "not a job id: $j" >&2; exit 2; }; done
  command -v squeue >/dev/null 2>&1 || { echo "squeue not found: this is not a SLURM host" >&2; exit 2; }
}

queued() { [ "$(squeue -h -j "$(IFS=,; echo "$*")" -o %i 2>/dev/null | wc -l)" -gt 0 ]; }

# Poll until the jobs leave the queue; print the report; return 0 if all COMPLETED.
wait_jobs() {
  while queued "$@"; do sleep "$POLL"; done
  report "$@"
}

report() {
  local ids j st rc=0
  ids=$(IFS=,; echo "$*")
  echo "open-science wait_slurm: jobs $ids have left the queue. Use open-science-context:continue-context if you have no context."
  for j in "$@"; do
    st=$(sacct -n -X -j "$j" -o State%20 2>/dev/null | head -1 | tr -d ' ')
    echo "job $j: ${st:-unknown}"
    [ "${st:-}" = COMPLETED ] || rc=1
  done
  return "$rc"
}

case "${1:-}" in
  --notify)
    shift; check_ids "$@"
    # shellcheck source=cm_lib.sh
    . "$HERE/cm_lib.sh"
    if cm_mod_active; then
      SID=$(cm_live_sid); [ -n "$SID" ] || { echo "no Claude session id found" >&2; exit 2; }
      F="$OS_STATE/wakers/$(cm_sess_key "$SID")/$(date +%s)-$$.json"
      cm_write_json "$F" --args --arg sid "$SID" --arg at "$(date -Iseconds)" \
          '{version:1, runtime:"claude-mod", jobs:$ARGS.positional, session_id:$sid, requested_at:$at, state:"requested"}' "$@" \
        || { echo "cannot write $F" >&2; exit 1; }
      echo "Waker queued for jobs $*: when they leave the queue, the open-science mod sends their states to"
      echo "this session (after a jump, to the new one), which starts a turn."
      exit 0
    fi
    [ "$(cm_runtime)" = codex ] || { echo "the open-science mod is not loaded, so --notify cannot wake this Claude Code session; run 'wait_slurm.sh <jobid>...' as a background Bash task instead" >&2; exit 4; }
    TID="${CODEX_THREAD_ID:-}"; [ -n "$TID" ] || { echo "CODEX_THREAD_ID is not set" >&2; exit 2; }
    cm_state_writable || { cm_state_hint >&2; exit 3; }
    KEY=$(cm_pane_key) || KEY=""
    TARGET="${KEY:-thread__$(cm_key "$TID")}"
    F="$OS_STATE/wakers/$TARGET/$(date +%s)-$$.json"
    T="${TMUX:-}"
    cm_write_json "$F" --args --arg tid "$TID" --arg key "$KEY" --arg sock "${T%%,*}" --arg pane "${TMUX_PANE:-}" \
        --arg at "$(date -Iseconds)" \
        '{version:1, runtime:"codex", jobs:$ARGS.positional, thread_id:$tid, key:$key, sock:$sock,
          pane:$pane, requested_at:$at, state:"requested"}' "$@" \
      || { echo "cannot write $F (in the Codex sandbox, add $OS_STATE as a writable root)" >&2; exit 1; }
    echo "Waker queued for jobs $*: it starts when this turn ends and, when the jobs leave the queue,"
    echo "sends their states to the Codex thread then running in ${TMUX_PANE:-this session} (codex queue)."
    exit 0 ;;
  --check)
    # For the mod: one poll of a queued Claude waker. Exit 10 while a job is queued or
    # running; otherwise print the report, archive the request, and exit 0 (all COMPLETED)
    # or 1.
    F="${2:-}"; [ -f "$F" ] || { echo "no waker request: $F" >&2; exit 2; }
    # shellcheck source=cm_lib.sh
    . "$HERE/cm_lib.sh"
    mapfile -t JOBS < <(jq -r '.jobs[]' "$F")
    check_ids "${JOBS[@]}"
    queued "${JOBS[@]}" && exit 10
    report "${JOBS[@]}"; rc=$?
    mkdir -p "$OS_STATE/wakers/done"
    jq --arg at "$(date -Iseconds)" '.state="done" | .finished_at=$at' "$F" \
      > "$OS_STATE/wakers/done/$(basename "$(dirname "$F")")-$(basename "$F")" && rm -f "$F"
    exit "$rc" ;;
  --run-waker)
    F="${2:-}"; [ -f "$F" ] || { echo "no waker request: $F" >&2; exit 2; }
    # shellcheck source=cm_lib.sh
    . "$HERE/cm_lib.sh"
    cm_json_update "$F" '.pid=($p|tonumber) | .pid_start=$s' --arg p "$$" --arg s "$(cm_proc_start $$)" || true
    mapfile -t JOBS < <(jq -r '.jobs[]' "$F")
    check_ids "${JOBS[@]}"
    report=$(wait_jobs "${JOBS[@]}"); rc=$?
    KEY=$(jq -r '.key // ""' "$F"); TID=$(jq -r .thread_id "$F")
    if [ -n "$KEY" ]; then
      # A jump in flight in this pane (the usual case: the wait jump this waker was queued
      # for) is about to replace the thread. Deliver only once its request is resolved
      # (done, failed or unconfirmed: the file leaves jump/), so the report reaches the
      # session that will be running, not the one being ended. Bounded by
      # $OPSCI_WAKER_JUMP_WAIT seconds (default 300); after that, deliver anyway and log it.
      JREQ=$(cm_request_path "$KEY"); jw=0
      while [ -f "$JREQ" ] && [ "$jw" -lt "${OPSCI_WAKER_JUMP_WAIT:-300}" ]; do sleep 2; jw=$((jw+2)); done
      [ -f "$JREQ" ] && cm_log "waker $(basename "$F"): a jump in pane ${KEY} is still $(jq -r .phase "$JREQ" 2>/dev/null) after ${jw} s; delivering to the thread live now"
      PREC=$(cm_cx_pane_path "$KEY")
      cur=$(jq -r '.thread_id // ""' "$PREC" 2>/dev/null)
      cm_is_codex_proc "$(jq -r '.tui_pid // ""' "$PREC" 2>/dev/null)" "$(jq -r '.tui_start // ""' "$PREC" 2>/dev/null)" \
        && [ -n "$cur" ] && TID="$cur"
      [ "$TID" = "$cur" ] || cm_log "waker $(basename "$F"): no live Codex TUI in pane ${KEY}; queueing to thread ${TID:0:8} (delivered when it is resumed)"
    fi
    home=$(jq -r '.codex_home // ""' "$(cm_cx_thread_path "$TID")" 2>/dev/null)
    msg="[open-science] $(printf '%s' "$report" | tr '\n' ' ')"
    if ${home:+env CODEX_HOME="$home"} timeout 120 codex queue --thread "$TID" --message "$msg" >>"$OS_LOG" 2>&1; then
      state=done; cm_log "waker $(basename "$F"): queued the job report to thread ${TID:0:8}"
    else
      state=failed; cm_log "waker $(basename "$F"): codex queue to thread ${TID:0:8} FAILED; report: $msg"
    fi
    mkdir -p "$OS_STATE/wakers/done"
    (
      exec 8>"$F.lock"; flock -w 30 8
      jq --arg s "$state" --arg t "$TID" --arg at "$(date -Iseconds)" '.state=$s | .notified_thread=$t | .finished_at=$at' "$F" \
        > "$OS_STATE/wakers/done/$(basename "$(dirname "$F")")-$(basename "$F")" && rm -f "$F"
    )
    rm -f "$F.lock"
    exit "$rc" ;;
esac

check_ids "$@"
wait_jobs "$@"
