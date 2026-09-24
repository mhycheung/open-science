#!/bin/bash
# Usage: rr_coordinator.sh <job_id>
#
# One coordinator per SLURM job (elected via flock in rr_registry.sh). It:
#   * submits ONE successor as soon as the registry for this job is non-empty.
#     Config key queue_mode chooses how it is queued:
#       afterany (default)  --dependency=afterany:<job>; starts after this job ends.
#       early               --begin=<end - early_lead_seconds>, no dependency, so it
#                           becomes eligible (and gains age priority) while this job
#                           still runs. If it starts early it asks this coordinator
#                           for a final snapshot (handoff), then ends this job.
#   * scancels + rolls back the successor if the registry drains to empty;
#   * re-snapshots every registered session every snapshot_interval_seconds, which
#     also refreshes the pane -> session cache (the job the old SessionStart hook
#     did), so a live session whose state file disappears is still identified;
#   * at winddown_threshold_seconds of runway nudges ACTIVE Claude panes once;
#     from then on the open-science core's session jumps are inhibited for the
#     registered panes (inhibit_jump_<key> files, removed after the hop);
#   * at pause_threshold_seconds (or on a handoff request): takes the final
#     snapshot, notifies, runs optional hooks in $RR_HOME/hooks, exits.
# The successor (rr_successor.sh) rebuilds every registered session from the
# snapshots, carries the registrations forward and elects its own coordinator.
set -u
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/rr_common.sh"

JOB_ID="$1"
LOG="$RR_HOME/coordinator_${JOB_ID}.log"
rr_log() { echo "[$(date -Iseconds)] coord[$JOB_ID]: $*" >> "$LOG"; }
rr_ensure_config
INTERVAL="${RR_COORD_INTERVAL:-5}"

# Pane->session cache for this job: rr_resolve_claude refreshes it from every
# live state file it reads, so no agent has to self-register.
export RR_SELFREG_DIR="$RR_REG_ROOT/$JOB_ID/panes"

LOCK="$RR_LOCK_DIR/coordinator_${JOB_ID}.lock"
exec {LFD}>"$LOCK"
if ! flock -n "$LFD"; then rr_log "another coordinator holds the lock; exiting"; exit 0; fi
MARK="$RR_LOCK_DIR/coordinator_${JOB_ID}.running"; touch "$MARK"
trap 'rm -f "$MARK"' EXIT

# Re-snapshot every registered tmux session in this job into its snapshot dir.
# Authoritative capture: run at the wind-down threshold so it reflects the final
# layout and each Claude pane's live status. Safe to run anytime (tmux server is
# still alive until the job ends).
snapshot_registered() {
  local dir="$RR_REG_ROOT/$JOB_ID" f san sock name
  shopt -s nullglob
  for f in "$dir"/*.json; do
    san=$(basename "$f" .json)
    sock=$(jq -r '.tmux_socket' "$f"); name=$(jq -r '.tmux_session' "$f")
    if RR_SELFREG_DIR="$dir/panes" bash "$RR_SCRIPTS_DIR/rr_snapshot.sh" "$sock" "$name" "$dir/snapshot/$san.json" 2>>"$LOG"; then
      rr_log "re-snapshotted tmux session '$name' (socket $sock)"
    else
      rr_log "re-snapshot FAILED for '$name' (socket $sock); keeping prior snapshot"
    fi
  done
  shopt -u nullglob
}

# Wind-down nudge: sent ONCE, a configurable lead time before the deadline, to
# ACTIVE panes only (status busy/shell). Idle panes are left alone (never woken).
# The message is queued into the busy agent's input, so it lands at the agent's
# next natural breakpoint. It is self-contained -- an agent needs no prior
# knowledge to act on it.
# It takes the pane injection lock (rr_common.sh: rr_pane_lock) for the same
# reason every other injector does: a session jump's worker types C-u, then
# `/clear`, then Enter, and `/clear` only runs when it is FIRST in the input
# buffer. This nudge landing between those keystrokes turns the clear into an
# ordinary message, so nothing gets cleared and the agent believes it jumped when
# it did not. Rare -- the tripwires suppress jumps once the wind-down marker
# exists -- but the marker is written just before this runs, so a jump armed
# moments earlier is still in flight.
winddown_nudge() {
  local mins="$1" f sock name info cdir cpid sid status target widx pidx ppid paneid lock
  local rf="$RR_STABLE_SCRIPTS/rr_registry.sh"
  local msg="${2:-[slurm-resurrect] This SLURM job is ~${mins} min from its wall-clock limit and will be killed then. You will be AUTOMATICALLY resurrected and your work auto-continues even if killed mid-step, so you do not have to stop. BUT if the unit of work in hand clearly will NOT finish before the limit, prefer to wind down GRACEFULLY: bring subagents/shells to a clean stop, save a message to your future self with 'bash $rf note \"<what to do next>\"', then go idle and wait -- on resurrection you will be handed that message to restart. If you can finish soon, just finish.}"
  shopt -s nullglob
  for f in "$RR_REG_ROOT/$JOB_ID"/*.json; do
    sock=$(jq -r '.tmux_socket' "$f"); name=$(jq -r '.tmux_session' "$f")
    while IFS=$'\t' read -r widx pidx ppid paneid; do
      info=$(rr_resolve_claude "$ppid" "$paneid") || continue
      IFS='|' read -r cdir cpid sid status <<< "$info"
      case "$status" in
        busy|shell)
          target="$name:$widx.$pidx"
          lock=$(rr_pane_lock "$sock" "$target")
          (
            exec 9>"$lock"
            if ! flock -w 120 9; then
              rr_log "wind-down nudge SKIPPED for $target -- pane lock busy 120s (a jump worker holds it)"
              exit 0
            fi
            if tmux -S "$sock" send-keys -t "$target" "$msg" 2>/dev/null; then
              sleep 2; tmux -S "$sock" send-keys -t "$target" Enter 2>/dev/null
              rr_log "wind-down nudge -> $sock $target (status=$status)"
            fi
          )
          ;;
      esac
    done < <(tmux -S "$sock" list-panes -s -t "$name" \
               -F $'#{window_index}\t#{pane_index}\t#{pane_pid}\t#{pane_id}' 2>/dev/null)
  done
  shopt -u nullglob
}

# ---------------------------------------------------------------------------
# The generated batch script is a thin wrapper: all logic lives in
# rr_successor.sh, read from $RR_HOME/scripts when the job RUNS. SLURM copies a
# batch script at submit time, so anything inlined here would be frozen for an
# already-queued successor.
generate_successor_script() {
  local hop="$1" tl="$2" part="$3" acct="$4" nodes="$5" ntasks="$6" cpt="$7"
  local script="$RR_HOME/rr_respawn_from_${JOB_ID}.slurm"
  {
    echo "#!/bin/bash"
    [[ -n "$part" && "$part" != "null" ]] && echo "#SBATCH -p $part"
    [[ -n "$acct" && "$acct" != "null" ]] && echo "#SBATCH -A $acct"
    echo "#SBATCH -N ${nodes:-1}"
    echo "#SBATCH -n ${ntasks:-1}"
    [[ -n "$cpt" && "$cpt" != "null" ]] && echo "#SBATCH -c $cpt"
    echo "#SBATCH -t $tl"
    echo "#SBATCH -J rr_respawn_h${hop}"
    echo "#SBATCH -o $RR_HOME/rr_respawn_from_${JOB_ID}.out"
    echo ""
    echo "export RR_STATE_DIR=\"$RR_HOME\""
    echo "exec bash \"\$RR_STATE_DIR/scripts/rr_successor.sh\" $JOB_ID"
  } > "$script"
  chmod +x "$script"; echo "$script"
}

SUBMIT_FAIL_AT=0
submit_successor() {  # <seconds left in this job>
  local left_s="$1" count max hop tl part acct nodes ntasks cpt script new mode lead
  local -a dep extra
  count=$(rr_cfg_get '.resurrection_count'); max=$(rr_cfg_get '.max_resurrections')
  hop=$((count+1)); tl=$(rr_cfg_get '.default_time_limit')
  part=$(rr_cfg_get '.partition // empty'); acct=$(rr_cfg_get '.account // empty')
  nodes=$(rr_cfg_get '.nodes // 1'); ntasks=$(rr_cfg_get '.ntasks // 1')
  cpt=$(rr_cfg_get '.cpus_per_task // empty')
  mode=$(rr_cfg_get '.queue_mode // "afterany"')
  read -ra extra <<< "$(rr_cfg_get '.sbatch_extra // ""')"
  rr_sync_scripts || rr_log "WARNING: could not refresh $RR_STABLE_SCRIPTS"
  script=$(generate_successor_script "$hop" "$tl" "$part" "$acct" "$nodes" "$ntasks" "$cpt")
  if [[ "$mode" == "early" ]]; then
    local now begin
    lead=$(rr_cfg_get '.early_lead_seconds // 14400')
    now=$(date +%s); begin=$(( now + left_s - lead ))
    (( begin < now )) && begin=$now
    dep=(--begin="$(date -d "@$begin" +%Y-%m-%dT%H:%M:%S)")
  else
    mode=afterany
    dep=(--dependency="afterany:${JOB_ID}")
  fi
  # Environment hygiene: sbatch exports this process's environment. Drop the
  # tmux and Claude Code variables so the successor starts clean tmux servers and
  # the rebuilt panes do not look like they run inside a Claude session.
  new=$(env -u TMUX -u TMUX_PANE -u CLAUDECODE -u RR_CALLER -u CLAUDE_CODE_SESSION_ID \
        -u CLAUDE_CODE_ENTRYPOINT sbatch "${dep[@]}" "${extra[@]}" --parsable "$script" 2>>"$LOG")
  new="${new%%;*}"
  if [[ ! "$new" =~ ^[0-9]+$ ]]; then
    SUBMIT_FAIL_AT=$(date +%s)
    rr_log "sbatch FAILED (mode $mode, got '${new}'); retrying in 300s"
    rr_notify "slurm-resurrect: could not queue a successor from job ${JOB_ID} (sbatch failed; see $LOG). Will retry."
    return 1
  fi
  rr_cfg_set ".resurrection_count += 1 | .job_lineage += [${new}] | .pending_resurrection_jobid = ${new}"
  rr_log "submitted successor $new (hop $hop/$max, limit $tl, mode $mode: ${dep[*]})"
  rr_notify "Resurrection queued from job ${JOB_ID}: successor ${new} (hop ${hop}/${max}, ${mode}) will rebuild $(rr_reg_count "$JOB_ID") tmux session(s)."
  [[ $hop -ge $max ]] && rr_notify "Note: successor ${new} is the LAST allowed resurrection (${hop}/${max}). The user can run 'reset' for more."
  return 0
}

# The successor started while this job is still running (queue_mode early): take
# the final snapshot now, tell it we are done, and exit. The successor then
# scancels this job and rebuilds.
handoff() {
  local grace; grace=$(rr_cfg_get '.handoff_grace_seconds // 0')
  local who; who=$(cat "$RR_HOME/handoff_${JOB_ID}.request" 2>/dev/null)
  rr_log "handoff requested by successor ${who:-?}"
  inhibit_jumps
  if [[ "$grace" =~ ^[0-9]+$ && "$grace" -gt 0 ]]; then
    winddown_nudge "$(( (grace + 59) / 60 ))" "[slurm-resurrect] The successor SLURM job has started, and this job will be ended in about $(( (grace + 59) / 60 )) min so your session can move to it. You will be resumed automatically. If the work in hand will not reach a clean stop by then, save a message to your future self with 'bash $RR_STABLE_SCRIPTS/rr_registry.sh note \"<what to do next>\"' and go idle."
    sleep "$grace"
  fi
  snapshot_registered
  date -Iseconds > "$RR_HOME/handoff_${JOB_ID}.done"
  rr_notify "Job ${JOB_ID}: handed off to successor ${who:-?}, which started early; it will end this job and rebuild $(rr_reg_count "$JOB_ID") tmux session(s)."
  run_pause_hooks
  rr_log "coordinator exiting after handoff"; exit 0
}

run_pause_hooks() {
  local hook
  for hook in "$RR_HOOK_DIR/on_pause.sh" "$RR_HOOK_DIR/on_pause_${JOB_ID}.sh"; do
    [[ -x "$hook" ]] && { rr_log "running hook $hook"; bash "$hook" "$JOB_ID" >> "$LOG" 2>&1 || true; }
  done
}

cancel_successor() {
  local pending; pending=$(rr_cfg_get '.pending_resurrection_jobid // empty')
  [[ -z "$pending" || "$pending" == "null" ]] && return 0
  scancel "$pending" 2>/dev/null
  rr_cfg_set '.pending_resurrection_jobid=null | .resurrection_count=(if .resurrection_count>0 then .resurrection_count-1 else 0 end)'
  rr_log "cancelled pending $pending (empty registry / stop)"
  rr_notify "Job ${JOB_ID}: registry empty / stopped -- cancelled pending successor ${pending}."
}

# Jumps started now would be cut off by the limit; the core refuses them while
# these files exist and prints this text.
INHIBIT_MSG="slurm-resurrect: this SLURM job (${JOB_ID}) is near its wall-clock limit and the session will be resurrected in a new job. Do not jump now. If the work in hand will not finish, save a note for your future self with: bash $RR_STABLE_SCRIPTS/rr_registry.sh note \"<what to do next>\", then go idle."
inhibit_jumps() { [[ -f "$RR_HOME/inhibit_${JOB_ID}.list" ]] && return 0; rr_inhibit_panes "$JOB_ID" "$INHIBIT_MSG"; rr_log "session jumps inhibited for the registered panes"; }

LAST_SNAP=$(date +%s)

rr_log "coordinator started for job $JOB_ID"
rr_inhibit_sweep "$JOB_ID"

# Sanitize stale state from the previous hop: the config's pending pointer may
# still name THIS job (the successor the OLD coordinator submitted). Clear it so
# this coordinator can queue the next hop.
stale_pending=$(rr_cfg_get '.pending_resurrection_jobid // empty')
if [[ -n "$stale_pending" && "$stale_pending" == "$JOB_ID" ]]; then
  rr_cfg_set '.pending_resurrection_jobid=null'
  rr_log "cleared stale pending_resurrection_jobid=$stale_pending (it is this job)"
fi

while true; do
  read -r left total <<< "$(squeue -h -j "$JOB_ID" -o '%L %l' 2>/dev/null)"
  [[ -z "${left:-}" ]] && { rr_log "job gone; exiting"; exit 0; }
  left_s=$(rr_parse_time_to_seconds "$left"); total_s=$(rr_parse_time_to_seconds "$total")

  stop=$(rr_cfg_get '.stop_requested'); count=$(rr_cfg_get '.resurrection_count')
  max=$(rr_cfg_get '.max_resurrections'); pending=$(rr_cfg_get '.pending_resurrection_jobid // empty')
  n=$(rr_reg_count "$JOB_ID")

  # Self-heal a stale pending pointer: if it names a job that is no longer in the
  # queue (dead lineage / cancelled successor), clear it so we can queue anew.
  # Safe because we only reach here with a working squeue (we just read $left).
  if [[ -n "$pending" && "$pending" != "null" ]] \
     && ! squeue -h -j "$pending" -o '%i' 2>/dev/null | grep -q .; then
    rr_log "pending successor $pending no longer in queue; clearing stale pointer"
    rr_cfg_set '.pending_resurrection_jobid=null'
    pending=null
  fi

  # Submit the (single) allowed successor when we may and none is pending.
  want_submit=false
  [[ "$stop" != "true" && $count -lt $max && $n -gt 0 ]] && want_submit=true
  # Cancel a pending successor ONLY because of a stop request or a drained
  # registry -- NOT merely because we've reached the resurrection ceiling. Once
  # the last allowed successor is queued, count==max makes want_submit false; if
  # we also cancelled on that condition we'd kill the legitimate final hop and
  # then resubmit next tick (submit/cancel oscillation, observed 2026-07-15).
  want_cancel=false
  [[ "$stop" == "true" || $n -eq 0 ]] && want_cancel=true
  if $want_submit && [[ -z "$pending" || "$pending" == "null" ]] \
     && (( $(date +%s) - SUBMIT_FAIL_AT >= 300 )); then submit_successor "$left_s"
  elif $want_cancel && [[ -n "$pending" && "$pending" != "null" ]]; then cancel_successor; fi

  th=$(rr_cfg_get '.pause_threshold_seconds')
  [[ -z "$th" || "$th" == "null" ]] && th=$(( total_s * 15 / 100 ))
  wd=$(rr_cfg_get '.winddown_threshold_seconds')
  [[ -z "$wd" || "$wd" == "null" ]] && wd=1800
  rr_log "left=${left}(${left_s}s) reg=${n} pending=${pending:-none} stop=${stop} count=${count}/${max} wd=${wd} th=${th}"

  # Wind-down nudge: once, when we cross the (earlier) wind-down threshold but are
  # still above the final pause threshold. Active panes only; idle panes untouched.
  if [[ $left_s -le $wd && $left_s -gt $th && ! -f "$RR_HOME/winddown_${JOB_ID}" && $n -gt 0 ]]; then
    touch "$RR_HOME/winddown_${JOB_ID}"
    inhibit_jumps
    rr_log "wind-down threshold reached (${left_s}s <= ${wd}s); nudging active panes"
    winddown_nudge "$(( left_s / 60 ))"
  fi

  # Handoff: an early-started successor wants the final snapshot now.
  [[ -f "$RR_HOME/handoff_${JOB_ID}.request" && ! -f "$RR_HOME/handoff_${JOB_ID}.done" ]] && handoff

  # Periodic snapshot: keeps the fallback snapshot and the pane cache current.
  si=$(rr_cfg_get '.snapshot_interval_seconds // 300')
  if [[ "$si" =~ ^[0-9]+$ && "$si" -gt 0 && $n -gt 0 ]] && (( $(date +%s) - LAST_SNAP >= si )); then
    snapshot_registered; LAST_SNAP=$(date +%s)
  fi

  if [[ $left_s -le $th ]]; then
    rr_log "pause threshold reached"
    touch "$RR_HOME/paused_${JOB_ID}"
    inhibit_jumps

    # Authoritative snapshot: capture final layout + live idle/busy status of
    # every registered tmux session before the job dies. This is what the
    # successor rebuilds from. (No interactive nudge: status is auto-detected,
    # so we don't need to disturb the panes.)
    snapshot_registered

    pending=$(rr_cfg_get '.pending_resurrection_jobid // empty')
    if [[ -n "$pending" && "$pending" != "null" ]]; then
      rr_notify "Job ${JOB_ID} within ${th}s of timeout. Successor ${pending} is queued; ${n} tmux session(s) will be rebuilt."
    else
      rr_notify "Job ${JOB_ID} within ${th}s of timeout. No successor queued (empty/disabled/limit)."
      rr_uninhibit "$JOB_ID"   # nothing will resume these panes; do not block their jumps
    fi
    run_pause_hooks
    rr_log "coordinator exiting after pause"; exit 0
  fi
  sleep "$INTERVAL"
done
