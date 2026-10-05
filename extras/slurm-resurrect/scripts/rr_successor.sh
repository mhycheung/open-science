#!/bin/bash
# Usage: rr_successor.sh <source_job_id>
#
# The body of a successor job. The batch script the coordinator submits only
# sets RR_STATE_DIR and execs this file, so a fix made here reaches a successor
# that is already queued.
#
#   1. If the source job is still RUNNING (queue_mode early: this job started
#      before the source ended), ask the source's coordinator for a final
#      snapshot, wait for it (handoff_timeout_seconds), then scancel the source
#      and wait for it to leave the queue.
#   2. Rebuild every tmux session from the source job's snapshots (rr_rebuild.sh:
#      each Claude pane with its own permission mode, at most the registered one,
#      and the registered Remote Control setting), on the recorded tmux socket if
#      its directory is private to this user, else on a fresh private one.
#   3. Carry the registrations forward to this job, deliver notes / interrupted
#      jumps (rr_deliver.sh), notify how to attach, elect this job's coordinator.
#      A session with no live Claude or Codex pane after delivery is not carried
#      further; if no session has one, the lineage ends here: no coordinator, no
#      successor, and the job exits.
#
# Test switches: RR_REBUILD_DRYRUN=1 / RR_DELIVER_DRYRUN=1 (no Claude started),
# RR_NO_COORDINATOR=1 (do not elect a coordinator), RR_SUCCESSOR_NO_HOLD=1 (exit
# instead of holding the job open), RR_HANDOFF_POLL (seconds, default 5).
set -u
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/rr_common.sh"

SRC_JOB="${1:?source job id required}"
SCRIPTS="$RR_SCRIPTS_DIR"
SRCDIR="$RR_REG_ROOT/$SRC_JOB"
SNAPDIR="$SRCDIR/snapshot"
JOB="${SLURM_JOB_ID:?SLURM_JOB_ID not set}"

# sbatch --export=ALL carries the submitting shell's environment. Start clean:
# no inherited tmux client, and nothing that marks the rebuilt panes as running
# inside a Claude Code session.
unset TMUX TMUX_PANE CLAUDECODE RR_CALLER CLAUDE_CODE_SESSION_ID CLAUDE_CODE_ENTRYPOINT \
      CODEX_THREAD_ID CODEX_SESSION_ID CODEX_CI

NODE=$(hostname)
DEFSOCK="${TMUX_TMPDIR:-/tmp}/tmux-$(id -u)/default"
echo "=== slurm-resurrect successor job $JOB on $NODE; source job $SRC_JOB ==="

# --- 1. handoff when the source job is still running ------------------------
src_state() { squeue -h -j "$SRC_JOB" -o '%T' 2>/dev/null | head -1; }
st=$(src_state)
if [[ "$st" == RUNNING || "$st" == SUSPENDED || "$st" == CONFIGURING ]]; then
  rr_ensure_config
  timeout=$(rr_cfg_get '.handoff_timeout_seconds // 180')
  grace=$(rr_cfg_get '.handoff_grace_seconds // 0')
  poll="${RR_HANDOFF_POLL:-5}"
  echo "source job $SRC_JOB is still $st: requesting a handoff (timeout ${timeout}s + grace ${grace}s)"
  echo "$JOB" | rr_write "$RR_HOME/handoff_${SRC_JOB}.request"
  waited=0; limit=$(( timeout + grace ))
  while [[ ! -f "$RR_HOME/handoff_${SRC_JOB}.done" && $waited -lt $limit ]]; do
    sleep "$poll"; waited=$((waited + poll))
  done
  if [[ -f "$RR_HOME/handoff_${SRC_JOB}.done" ]]; then
    echo "handoff: source coordinator took the final snapshot after ${waited}s"
  else
    echo "handoff: WARNING no answer from the source coordinator after ${waited}s; using the last periodic snapshot"
    rr_notify "slurm-resurrect: successor $JOB got no handoff answer from job $SRC_JOB within ${limit}s; ending it and rebuilding from the last periodic snapshot."
  fi
  scancel "$SRC_JOB" 2>/dev/null && echo "scancelled source job $SRC_JOB"
  for ((i=0; i<60; i++)); do
    st=$(src_state); [[ -z "$st" || "$st" == COMPLETED || "$st" == CANCELLED* || "$st" == FAILED || "$st" == TIMEOUT ]] && break
    sleep 2
  done
  echo "source job $SRC_JOB state now: ${st:-gone}"
fi

# "Don't start if empty" -- re-check the snapshots at runtime.
n=$(find "$SNAPDIR" -maxdepth 1 -name '*.json' -type f 2>/dev/null | wc -l)
if [[ "$n" -eq 0 ]]; then echo "no snapshots; nothing to respawn."; exit 0; fi

# --- 2. rebuild every tmux session; collect a manifest of the Claude panes ---
MANIFEST="$RR_HOME/rr_manifest_${JOB}.jsonl"; rr_nolink "$MANIFEST"; : > "$MANIFEST"
newdir="$RR_REG_ROOT/$JOB"; mkdir -p "$newdir/snapshot"
for snap in "$SNAPDIR"/*.json; do
  san=$(basename "$snap" .json)
  rec="$SRCDIR/$san.json"
  name=$(jq -r '.session_name' "$snap")
  # Rebuild on the session's ORIGINAL socket path so the attach command is unchanged.
  TSOCK=$(jq -r '.tmux_socket // empty' "$snap")
  [[ -n "$TSOCK" && "$TSOCK" != "null" ]] || TSOCK="$DEFSOCK"
  # Never start a server in a socket directory another user could control
  # (rr_safe_sock: falls back to a fresh private directory and tells the user).
  if ! TSOCK=$(rr_safe_sock "$TSOCK"); then
    echo "ERROR: no safe tmux socket for '$name'; not rebuilt"; continue
  fi
  # If that exact name is already live on that socket (node reuse, a surviving
  # server), rebuild under a suffixed name rather than clobbering it.
  RNAME="$name"
  if tmux -S "$TSOCK" has-session -t "=$name" 2>/dev/null; then
    RNAME="${name}-rr$JOB"
    echo "WARNING: session '$name' already exists on $TSOCK; rebuilding as '$RNAME'"
  fi
  echo "rebuilding tmux session '$RNAME' on socket $TSOCK from $snap"
  RR_REBUILD_NAME="$RNAME" RR_WRITE_SELFREG_DIR="$newdir/panes" \
    bash "$SCRIPTS/rr_rebuild.sh" "$snap" "$TSOCK" "$rec" >> "$MANIFEST"
  # Carry the registration forward -- with the user's settings -- so THIS job's
  # coordinator resurrects it next hop.
  if [[ -f "$rec" ]]; then
    jq --arg s "$RNAME" --arg sock "$TSOCK" --arg job "$JOB" --arg san "$san" \
      '.tmux_session=$s | .tmux_socket=$sock | .sanitized=$san | .registered_in_job=$job
       | .carried_at=(now|todate)' "$rec" | rr_write "$newdir/$san.json"
  else
    # No record of the user's choices: the narrow defaults (at most `manual`, Remote
    # Control off), never bypassPermissions.
    jq -n --arg s "$RNAME" --arg sock "$TSOCK" --arg job "$JOB" --arg san "$san" \
      '{tmux_session:$s, tmux_socket:$sock, sanitized:$san, registered_in_job:$job,
        registered_at:(now|todate), permission_mode:"manual", permission_mode_explicit:false,
        remote_control:false}' \
      | rr_write "$newdir/$san.json"
  fi
done
count=$(jq -s '[.[] | select((.runtime // "claude") == "claude" and .status != "unresumable")] | length' "$MANIFEST" 2>/dev/null); count=${count:-0}
xcount=$(jq -s '[.[] | select(.runtime == "codex" and .status != "unresumable")] | length' "$MANIFEST" 2>/dev/null); xcount=${xcount:-0}
xlost=$(jq -r 'select(.runtime == "codex" and .status == "unresumable") | "\(.target): \(.reason)"' "$MANIFEST" 2>/dev/null)
echo "launched $count Claude pane(s) and $xcount Codex pane(s); waiting for boot..."
[[ -n "$xlost" ]] && echo "Codex panes NOT resumed (left as shells):"$'\n'"$xlost"

# The source job's jump-inhibit files (rr_common.sh rr_inhibit_panes) have
# done their job; a rebuilt pane can get the same key, so remove them first.
rr_uninhibit "$SRC_JOB"

# Post-boot steps (trust dialog, interrupted jumps, notes, Remote Control audit).
bash "$SCRIPTS/rr_deliver.sh" "$MANIFEST"

# A registered session with no live Claude or Codex pane is not carried to the next
# job: holding an allocation for plain shells helps no one. If no session has one,
# every resume failed: tell the user and end the lineage here (no coordinator, so no
# successor; the job exits instead of holding the allocation). Skipped in a rebuild
# dry run, which starts no agent.
if [[ "${RR_REBUILD_DRYRUN:-0}" != "1" ]]; then
  alive_total=0; dropped=""
  for f in "$newdir"/*.json; do
    [[ -e "$f" ]] || continue
    name=$(jq -r '.tmux_session' "$f"); sock=$(jq -r '.tmux_socket' "$f"); alive=0
    for ((i=0; i<${RR_ALIVE_WAIT:-30}; i++)); do
      while IFS= read -r ppid; do
        rr_pane_agent_alive "$ppid" && { alive=1; break; }
      done < <(tmux -S "$sock" list-panes -s -t "=$name" -F '#{pane_pid}' 2>/dev/null)
      [[ $alive -eq 1 ]] && break
      sleep 1
    done
    if [[ $alive -eq 1 ]]; then alive_total=$((alive_total+1))
    else
      san=$(basename "$f" .json)
      rm -f "$f" "$newdir/snapshot/$san.json"
      dropped="$dropped '$name'"
      echo "no live Claude or Codex pane in '$name'; not carried to the next job"
    fi
  done
  if [[ $alive_total -eq 0 ]]; then
    rr_notify "slurm-resurrect: job $JOB on $NODE resumed no Claude or Codex session (sessions:${dropped:- none}). Resurrection stops here: no successor will be queued and this job ends now. Start the sessions by hand and register again if you need it."
    rm -rf "$SRCDIR"; rm -f "$RR_HOME/handoff_${SRC_JOB}.request" "$RR_HOME/handoff_${SRC_JOB}.done"
    echo "no live agent pane in any rebuilt session; ending the lineage"
    exit 0
  fi
  [[ -n "$dropped" ]] && rr_notify "slurm-resurrect: job $JOB: no live Claude or Codex pane in tmux session(s)$dropped after the resume; they stay open in this job but are not carried to the next one."
fi

# Fresh fallback snapshot for THIS job (post-boot, so panes are detectable).
for f in "$newdir"/*.json; do
  [[ -e "$f" ]] || continue
  san=$(basename "$f" .json); name=$(jq -r '.tmux_session' "$f")
  sock=$(jq -r '.tmux_socket' "$f")
  RR_SELFREG_DIR="$newdir/panes" bash "$SCRIPTS/rr_snapshot.sh" "$sock" "$name" "$newdir/snapshot/$san.json" 2>/dev/null || true
done

# --- 3. notify how to reach the resurrected session(s) -----------------------
connect="Respawned in job $JOB on node $NODE ($count Claude pane(s), $xcount Codex pane(s)). Attach in a terminal:"
[[ -n "$xlost" ]] && connect="$connect"$'\n'"Codex panes not resumed (start Codex there by hand):"$'\n'"$xlost"
for f in "$newdir"/*.json; do
  [[ -e "$f" ]] || continue
  name=$(jq -r '.tmux_session' "$f"); sock=$(jq -r '.tmux_socket' "$f")
  if [[ "$sock" == "$DEFSOCK" ]]; then
    connect="$connect"$'\n'"[$name] ssh $NODE, then:     tmux a -t '$name'"
    connect="$connect"$'\n'"      from a login node:    srun --jobid=$JOB --overlap --pty tmux attach -t '$name'"
  else
    connect="$connect"$'\n'"[$name] from a login node:  srun --jobid=$JOB --overlap --pty tmux -S $sock attach -t '$name'"
    connect="$connect"$'\n'"      or ssh direct:        ssh $NODE -t 'tmux -S $sock attach -t \"$name\"'"
  fi
done
RCF="$RR_HOME/rc_status_${JOB}.txt"
[[ -s "$RCF" ]] && connect="$connect"$'\n'"Remote Control:"$'\n'"$(cat "$RCF")"
rr_notify "$connect"
echo "sent connect-info notification"

# Consume the source registry (sessions already carried forward above).
rm -rf "$SRCDIR"; echo "cleared source registry for job $SRC_JOB"
rm -f "$RR_HOME/handoff_${SRC_JOB}.request" "$RR_HOME/handoff_${SRC_JOB}.done"

# Elect a coordinator for THIS job so the cycle keeps going.
if [[ "${RR_NO_COORDINATOR:-0}" != "1" ]]; then
  rr_nolink "$RR_HOME/coordinator_${JOB}.log"
  setsid nohup bash "$SCRIPTS/rr_coordinator.sh" "$JOB" \
    >> "$RR_HOME/coordinator_${JOB}.log" 2>&1 < /dev/null &
  echo "elected coordinator for job $JOB"
fi

[[ "${RR_SUCCESSOR_NO_HOLD:-0}" == "1" ]] && exit 0
# Hold the allocation: the rebuilt tmux servers live inside this job.
sleep infinity
