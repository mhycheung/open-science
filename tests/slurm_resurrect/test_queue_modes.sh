#!/bin/bash
# opsci: planted-leaks (fake SLURM job ids for the stub scheduler)
# Queueing and the end of a job, against the stub scheduler (lib.sh):
#   * afterany (default): the successor depends on the running job;
#   * early: the successor gets --begin = end - early_lead_seconds (floored at now);
#   * handoff: an early successor that starts while the source still runs gets a
#     final snapshot from the source's coordinator, then ends the source;
#     without an answer it times out, warns, and still ends the source;
#   * the hop cap stops submission;
#   * wind-down inhibits the core's session jumps for the registered Claude panes,
#     and the pause removes the inhibit when no successor will resume them.
# Each behaviour is paired with the case where it must not happen.
#
#   bash tests/slurm_resurrect/test_queue_modes.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
rr_test_setup
RR="$SCRIPTS/rr_registry.sh"
export SLURM_JOB_ID=500
export RR_COORD_INTERVAL=1

wait_for() {  # <seconds> <command...>: 0 once the command succeeds
  local t="$1" i; shift
  for ((i=0; i<t*5; i++)); do "$@" && return 0; sleep 0.2; done
  return 1
}
COORD=""
stop_coord() { [[ -n "$COORD" ]] && kill "$COORD" 2>/dev/null; wait "$COORD" 2>/dev/null; COORD=""; }
start_coord() { bash "$SCRIPTS/rr_coordinator.sh" 500 >/dev/null 2>&1 & COORD=$!; }

# new_case <name> <left> [set key value]...: a fresh lineage whose job 500 has
# <left> of runway, with one registered tmux session holding a Claude pane.
new_case() {
  local name="$1" left="$2"; shift 2
  stop_coord
  export RR_STATE_DIR="$TMP/st_$name" OPSCI_STATE_DIR="$TMP/os_$name"
  mkdir -p "$RR_STATE_DIR"; touch "$RR_STATE_DIR/warning_shown"
  rm -f "$STUB"/jobs/* "$STUB/sbatch.log" "$STUB/scancel.log"
  job 500 RUNNING "$left" 2:00:00
  start_tmux "$name"
  fake_claude "$name:0.0" "sid-$name" >/dev/null
  tmux -S "$SOCK" new-window -d -t "$name:1"
  in_pane "$name:1" "bash $RR register" >/dev/null
  while [[ $# -ge 2 ]]; do in_pane "$name:1" "bash $RR set $1 $2" >/dev/null; shift 2; done
  LOG="$RR_STATE_DIR/coordinator_500.log"
}
sbatch_args() { cat "$STUB/sbatch.log" 2>/dev/null; }
begin_epoch() { local b; b=$(grep -o -- '--begin=[^ ]*' "$STUB/sbatch.log" | head -1); date -d "${b#--begin=}" +%s 2>/dev/null; }
near() { local d=$(( $1 - $2 )); (( d < 0 )) && d=$(( -d )); (( d <= $3 )); }

echo "== 1. afterany (default) =="
new_case qa 1:00:00
start_coord
wait_for 20 test -s "$STUB/sbatch.log" && ok "a successor was submitted" || bad "submit" "$(tail -3 "$LOG")"
has   "depends on the running job" "$(sbatch_args)" "--dependency=afterany:500"
hasnt "no --begin in afterany mode" "$(sbatch_args)" "--begin"
check "exactly one successor" "$(grep -c . "$STUB/sbatch.log")" "1"
check "count and pending recorded" "$(jq -r '"\(.resurrection_count) \(.pending_resurrection_jobid)"' "$RR_STATE_DIR/rr_config.json")" "1 900001"
[[ ! -e "$RR_STATE_DIR/inhibit_500.list" ]] && ok "far from the limit: no jump inhibit" || bad "inhibit too early" ""
stop_coord

echo "== 2. early: --begin = end - lead =="
new_case qe 1:00:00 queue_mode early early_lead_seconds 600
now=$(date +%s); start_coord
wait_for 20 test -s "$STUB/sbatch.log" || bad "submit" "$(tail -3 "$LOG")"
hasnt "no dependency in early mode" "$(sbatch_args)" "--dependency"
b=$(begin_epoch)
near "${b:-0}" $(( now + 3600 - 600 )) 30 && ok "begin is now + left - lead (±30 s)" \
  || bad "begin time" "got $(date -d "@${b:-0}" 2>/dev/null), want ~$(date -d "@$((now+3000))")"
stop_coord

echo "== 3. early, lead longer than the runway: begin floored at now =="
new_case qf 1:00:00 queue_mode early early_lead_seconds 7200
now=$(date +%s); start_coord
wait_for 20 test -s "$STUB/sbatch.log" || bad "submit" "$(tail -3 "$LOG")"
b=$(begin_epoch)
near "${b:-0}" "$now" 30 && ok "begin is now (±30 s), never in the past" || bad "begin floor" "got ${b:-none}, now $now"
stop_coord

echo "== 4. hop cap reached: nothing submitted =="
new_case qc 1:00:00
jq '.resurrection_count=3 | .max_resurrections=3' "$RR_STATE_DIR/rr_config.json" > "$TMP/c" && mv "$TMP/c" "$RR_STATE_DIR/rr_config.json"
start_coord
wait_for 20 grep -qs "count=3/3" "$LOG" && sleep 3
[[ ! -s "$STUB/sbatch.log" ]] && ok "no successor at the cap" || bad "cap ignored" "$(sbatch_args)"
stop_coord

echo "== 5. handoff: early successor starts while the source runs =="
new_case qh 1:00:00 queue_mode early
start_coord
wait_for 20 test -s "$STUB/sbatch.log"
out=$(SLURM_JOB_ID=600 RR_HANDOFF_POLL=1 RR_REBUILD_DRYRUN=1 RR_DELIVER_DRYRUN=1 RR_SUCCESSOR_NO_HOLD=1 \
      bash "$SCRIPTS/rr_successor.sh" 500 2>&1)
has "successor asked for a handoff"           "$out" "requesting a handoff"
has "source coordinator answered"             "$out" "took the final snapshot"
has "source job ended by the successor"       "$(cat "$STUB/scancel.log" 2>/dev/null)" "500"
has "sessions rebuilt"                        "$out" "rebuilding tmux session 'qh"
has "coordinator took the handoff path"       "$(cat "$LOG")" "coordinator exiting after handoff"
has "jumps were inhibited during the handoff" "$(cat "$LOG")" "session jumps inhibited"
[[ ! -e "$RR_STATE_DIR/inhibit_500.list" ]] && [[ -z "$(ls "$OPSCI_STATE_DIR"/inhibit_jump_* 2>/dev/null)" ]] \
  && ok "inhibit removed after the hop" || bad "inhibit left behind" "$(ls "$OPSCI_STATE_DIR")"
wait "$COORD" 2>/dev/null; COORD=""

echo "== 6. control: source already gone -> no handoff, nothing cancelled =="
new_case qg 1:00:00
rm -f "$STUB/jobs/500"
out=$(SLURM_JOB_ID=601 RR_REBUILD_DRYRUN=1 RR_DELIVER_DRYRUN=1 RR_SUCCESSOR_NO_HOLD=1 \
      bash "$SCRIPTS/rr_successor.sh" 500 2>&1)
hasnt "no handoff request" "$out" "requesting a handoff"
[[ ! -e "$RR_STATE_DIR/handoff_500.request" && ! -s "$STUB/scancel.log" ]] \
  && ok "no request file, no scancel" || bad "unexpected handoff" "$(cat "$STUB/scancel.log" 2>/dev/null)"
has "still rebuilt" "$out" "rebuilding tmux session 'qg"

echo "== 7. handoff timeout: no coordinator answers =="
new_case qt 1:00:00 handoff_timeout_seconds 3
out=$(SLURM_JOB_ID=602 RR_HANDOFF_POLL=1 RR_REBUILD_DRYRUN=1 RR_DELIVER_DRYRUN=1 RR_SUCCESSOR_NO_HOLD=1 \
      bash "$SCRIPTS/rr_successor.sh" 500 2>&1)
has "warns that nobody answered"         "$out" "WARNING no answer from the source coordinator"
has "still ends the source job"          "$(cat "$STUB/scancel.log" 2>/dev/null)" "500"
has "rebuilds from the periodic snapshot" "$out" "rebuilding tmux session 'qt"
has "user notified" "$(cat "$RR_STATE_DIR/notifications.log")" "got no handoff answer"

echo "== 8. wind-down: jumps inhibited for the Claude pane, idle pane not nudged =="
new_case qw 0:20:00
start_coord
wait_for 20 test -e "$RR_STATE_DIR/inhibit_500.list" && ok "inhibit list written at wind-down" || bad "wind-down" "$(tail -3 "$LOG")"
QW_PANE=$(tmux -S "$SOCK" display-message -p -t qw:0.0 '#{pane_id}')
KEYF="$OPSCI_STATE_DIR/inhibit_jump_$(printf '%s' "$SOCK" | tr -c 'A-Za-z0-9._-' '_')__$(printf '%s' "$QW_PANE" | tr -c 'A-Za-z0-9._-' '_')"
has "inhibit file for the Claude pane, in the core's key format" "$(cat "$KEYF" 2>/dev/null)" "near its wall-clock limit"
check "only the Claude pane is inhibited" "$(grep -c . "$RR_STATE_DIR/inhibit_500.list")" "1"
hasnt "idle Claude pane not nudged" "$(cat "$LOG")" "wind-down nudge ->"
stop_coord

echo "== 9. pause with no successor (stopped): inhibit removed =="
new_case qp 0:01:00
in_pane qp:1 "bash $RR stop" >/dev/null
start_coord
wait_for 20 grep -qs "coordinator exiting after pause" "$LOG" && ok "coordinator paused and exited" || bad "pause" "$(tail -3 "$LOG")"
[[ ! -s "$STUB/sbatch.log" ]] && ok "stopped lineage submitted nothing" || bad "stop ignored" "$(sbatch_args)"
[[ ! -e "$RR_STATE_DIR/inhibit_500.list" && -z "$(ls "$OPSCI_STATE_DIR"/inhibit_jump_* 2>/dev/null)" ]] \
  && ok "no successor -> jumps not left inhibited" || bad "inhibit left" "$(ls "$OPSCI_STATE_DIR")"
wait "$COORD" 2>/dev/null; COORD=""

echo "== 10. control: pause with a successor queued keeps the inhibit =="
new_case qq 0:01:00
start_coord
wait_for 20 grep -qs "coordinator exiting after pause" "$LOG" || bad "pause" "$(tail -3 "$LOG")"
test -s "$STUB/sbatch.log" && ok "successor queued" || bad "no successor" "$(tail -3 "$LOG")"
[[ -e "$RR_STATE_DIR/inhibit_500.list" ]] && ok "inhibit kept until the successor removes it" || bad "inhibit dropped early" ""
wait "$COORD" 2>/dev/null; COORD=""

finish
