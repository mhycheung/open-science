#!/bin/bash
# One real resurrection hop per queue mode, on the real scheduler. Manual test:
# it submits SLURM jobs and uses a little allocation (at most 60 core-minutes on a
# 1-core shared partition: four jobs of 15 minutes). Queue waits do not count
# against the checks; RR_TEST_QUEUE_WAIT (s, default 6 h) bounds them.
#
#   bash tests/slurm_resurrect/real_hop.sh <workdir> [afterany|early|both]
#
# <workdir> must be on a filesystem that compute nodes share with this host.
# Account and partition come from RR_TEST_ACCOUNT / RR_TEST_PARTITION, else from
# the SLURM job this runs in, else the scheduler's defaults.
#
# For each mode a source job starts a private tmux server (socket under the
# node's TMPDIR), a pane with a stand-in Claude (a process named `claude` with a
# sessions/<pid>.json), and registers the session from a plain pane the way a
# user would: first run shows the warning, second run registers, with
# --permission-mode acceptEdits --remote-control off. The launch command is a
# recorder, so no real Claude runs. The hop cap is 1, so the successor queues
# nothing further.
#   afterany: the successor waits for the source to hit its time limit.
#   early:    the successor is eligible at once (early_lead_seconds > limit), starts
#             while the source runs, asks for a handoff, and ends the source.
# Checks, per mode: the successor rebuilt the session on its node (tmux list-panes
# through srun --overlap), the recorder saw --resume <sid> --permission-mode
# acceptEdits and no --remote-control, and (early) the source was ended by the
# successor after a handoff. The script then cancels the successors it created,
# and only those, and prints sacct lines for every job it submitted.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN="$(cd "$HERE/../../extras/slurm-resurrect" && pwd)"
WD="${1:?usage: real_hop.sh <workdir> [afterany|early|both]}"; MODES="${2:-both}"
[[ "$MODES" == both ]] && MODES="afterany early"
command -v sbatch >/dev/null || { echo "sbatch not found: not a SLURM host"; exit 2; }
mkdir -p "$WD"; WD="$(cd "$WD" && pwd)"
ACCT="${RR_TEST_ACCOUNT:-${SLURM_JOB_ACCOUNT:-}}"; PART="${RR_TEST_PARTITION:-${SLURM_JOB_PARTITION:-}}"
# 15 minutes, not less: on a busy controller each squeue/sbatch from the compute
# node can stall for minutes (measured 2026-09-24: registration took 3.4 min and
# the coordinator's first squeue about 3 min), which used up a 6-minute source job
# before its coordinator could queue the successor.
LIMIT="${RR_TEST_LIMIT:-00:15:00}"
# Registration to successor submission; the same stalls apply.
SUCC_WAIT="${RR_TEST_SUCC_WAIT:-600}"
# The early-mode source runs longer, so its successor has time to get through
# the queue while the source still runs (the only way to exercise the handoff).
EARLY_LIMIT="${RR_TEST_EARLY_LIMIT:-00:15:00}"
# How long to wait for a job to leave the queue. The fixed waits below start
# only once the job runs, so a busy queue does not fail the test.
QUEUE_WAIT="${RR_TEST_QUEUE_WAIT:-21600}"
pass=0; fail=0
ok()  { echo "  PASS  $1"; pass=$((pass+1)); }
bad() { echo "  FAIL  $1"; echo "        $2"; fail=$((fail+1)); }
SUBMITTED=()

make_source_script() {  # <mode> <dir> -> path of the batch script
  local mode="$1" d="$2" run="rrhop.$$.$1" early=""
  [[ "$mode" == early ]] && early='bash \$RRS set queue_mode early; bash \$RRS set early_lead_seconds 3600;'
  mkdir -p "$d/bin" "$d/cfg/sessions" "$d/cfg/projects/p" "$d/slurm"
  cp "$(command -v sleep)" "$d/bin/claude"
  cat > "$d/bin/recorder" <<REC
#!/bin/bash
out="$d/launch.\$(hostname -s).\$\$"
{ echo "CCD=\${CLAUDE_CONFIG_DIR:-}"; echo "NAME=\${CLAUDE_CODE_SESSION_NAME:-}"
  printf 'ARG=%s\n' "\$@"; } > "\$out.tmp" && mv "\$out.tmp" "\$out"
exec "$d/bin/claude" 3600
REC
  chmod +x "$d/bin/recorder"
  jq -nc '{type:"assistant", isSidechain:false, message:{model:"claude-hop-test"}}' > "$d/cfg/projects/p/sid-hop-$mode.jsonl"
  cat > "$d/source.sbatch" <<SB
#!/bin/bash
#SBATCH -J rrhop_src_$mode
#SBATCH -t $([[ "$mode" == early ]] && echo "$EARLY_LIMIT" || echo "$LIMIT")
#SBATCH -n 1
#SBATCH -o $d/slurm/source_%j.out
${ACCT:+#SBATCH -A $ACCT}
${PART:+#SBATCH -p $PART}
unset CLAUDECODE TMUX TMUX_PANE CLAUDE_CODE_SESSION_ID CLAUDE_CODE_ENTRYPOINT RR_CALLER
SOCK="\${TMPDIR:-/tmp}/$run/tmux.sock"; mkdir -p "\$(dirname "\$SOCK")"; chmod 700 "\$(dirname "\$SOCK")"
echo "\$SOCK" > "$d/socket"
tmux -S "\$SOCK" -f /dev/null new-session -d -s hop -x 200 -y 50
PPID0=\$(tmux -S "\$SOCK" display-message -p -t hop:0.0 '#{pane_pid}')
tmux -S "\$SOCK" send-keys -t hop:0.0 "CLAUDE_CONFIG_DIR=$d/cfg exec $d/bin/claude 3600" Enter
for i in \$(seq 50); do [ "\$(ps -o comm= -p \$PPID0)" = claude ] && break; sleep 0.2; done
printf '{"sessionId":"sid-hop-$mode","status":"idle"}\n' > "$d/cfg/sessions/\$PPID0.json"
tmux -S "\$SOCK" new-window -d -t hop:1
tmux -S "\$SOCK" send-keys -t hop:1 "export RR_STATE_DIR=$d/state OPSCI_STATE_DIR=$d/os RR_CONFIG_DIRS=$d/cfg RR_BOOT_WAIT=5 RR_SETTLE_WAIT=2 RR_COORD_INTERVAL=5 RRS=$PLUGIN/scripts/rr_registry.sh; bash \\\$RRS reset 1; bash \\\$RRS set launch_cmd $d/bin/recorder; bash \\\$RRS set pause_threshold_seconds 90; bash \\\$RRS set winddown_threshold_seconds 150; bash \\\$RRS set snapshot_interval_seconds 30; $early bash \\\$RRS register > $d/reg1.out 2>&1; echo \\\$? > $d/reg1.rc; bash \\\$RRS register --permission-mode acceptEdits --remote-control off > $d/reg2.out 2>&1; echo \\\$? > $d/reg2.rc" Enter
sleep infinity
SB
  echo "$d/source.sbatch"
}

wait_until() {  # <seconds> <description> <command...>
  local t="$1" what="$2" i; shift 2
  for ((i=0; i<t; i+=10)); do "$@" && return 0; sleep 10; done
  echo "  timed out after ${t}s waiting for: $what"; return 1
}
succ_of() { jq -r '.job_lineage[0] // empty' "$1/state/rr_config.json" 2>/dev/null; }
has_succ() { [[ -n "$(succ_of "$1")" ]]; }  # re-evaluated on every poll; "$(...)" as an argument is expanded once
launched_on_succ() { ls "$1"/launch.* >/dev/null 2>&1; }
job_state() { sacct -n -X -j "$1" -o State%20 2>/dev/null | head -1 | tr -d ' '; }
left_queue() { local s; s=$(job_state "$1"); [[ -n "$s" && "$s" != PENDING ]]; }
start_of() { sacct -n -X -j "$1" -o Start 2>/dev/null | head -1 | tr -d ' '; }
end_of()   { sacct -n -X -j "$1" -o End 2>/dev/null | head -1 | tr -d ' '; }

declare -A SRC SUCC
for m in $MODES; do
  d="$WD/$m"; rm -rf "$d"; mkdir -p "$d"
  s=$(make_source_script "$m" "$d")
  id=$(env -u CLAUDECODE sbatch --parsable "$s"); id="${id%%;*}"
  [[ "$id" =~ ^[0-9]+$ ]] || { echo "sbatch failed for $m: $id"; exit 1; }
  SRC[$m]=$id; SUBMITTED+=("$id"); echo "[$m] source job $id submitted"
done

for m in $MODES; do
  d="$WD/$m"; echo "== $m =="
  wait_until "$QUEUE_WAIT" "[$m] source job ${SRC[$m]} to start" left_queue "${SRC[$m]}" || { bad "[$m] source never started" "$(job_state "${SRC[$m]}")"; continue; }
  if wait_until 900 "[$m] registration" test -s "$d/reg2.rc"; then
    [[ "$(cat "$d/reg1.rc")" == 4 ]] && ok "[$m] first register shows the warning only (exit 4)" || bad "[$m] warning" "$(cat "$d/reg1.out")"
    [[ "$(cat "$d/reg2.rc")" == 0 ]] && ok "[$m] second register registers" || bad "[$m] register" "$(cat "$d/reg2.out")"
  else bad "[$m] registration" "see $d/slurm"; continue; fi
  wait_until "$SUCC_WAIT" "[$m] successor submitted" has_succ "$d" || { bad "[$m] no successor" "$(tail -5 "$d/state/"coordinator_*.log)"; continue; }
  SUCC[$m]=$(succ_of "$d"); SUBMITTED+=("${SUCC[$m]}"); echo "  successor ${SUCC[$m]}"
  dep=$(scontrol show job "${SUCC[$m]}" 2>/dev/null | grep -o 'Dependency=[^ ]*')
  if [[ "$m" == afterany ]]; then
    [[ "$dep" == *"afterany:${SRC[$m]}"* ]] && ok "[$m] successor depends on the source ($dep)" || bad "[$m] dependency" "$dep"
  else
    [[ "$dep" != *afterany* ]] && ok "[$m] successor has no dependency" || bad "[$m] dependency" "$dep"
  fi
done

for m in $MODES; do
  d="$WD/$m"; [[ -n "${SUCC[$m]:-}" ]] || continue; echo "== $m: the hop =="
  wait_until "$QUEUE_WAIT" "[$m] successor ${SUCC[$m]} to start" left_queue "${SUCC[$m]}" || { bad "[$m] successor never started" "$(job_state "${SUCC[$m]}")"; continue; }
  if ! wait_until 1500 "[$m] rebuilt session launched" launched_on_succ "$d"; then
    bad "[$m] no launch recorded" "$(tail -20 "$d/state/"rr_respawn_from_*.out 2>/dev/null)"; continue
  fi
  sleep 20
  L=$(cat "$d"/launch.* | head -40)
  [[ "$L" == *$'ARG=--resume\nARG=sid-hop-'"$m"* ]] && ok "[$m] resumed the recorded session id" || bad "[$m] resume" "$L"
  [[ "$L" == *$'ARG=--permission-mode\nARG=acceptEdits'* ]] && ok "[$m] permission mode carried" || bad "[$m] perm" "$L"
  [[ "$L" != *"ARG=--remote-control"* ]] && ok "[$m] Remote Control off carried" || bad "[$m] rc" "$L"
  [[ "$L" == *"CCD=$d/cfg"* ]] && ok "[$m] CLAUDE_CONFIG_DIR restored" || bad "[$m] ccd" "$L"
  sock=$(cat "$d/socket")
  panes=$(srun --jobid="${SUCC[$m]}" --overlap -n1 tmux -S "$sock" list-panes -a -F '#{session_name}:#{window_index}.#{pane_index}' 2>&1)
  [[ "$panes" == *"hop:0.0"* && "$panes" == *"hop:1.0"* ]] && ok "[$m] successor node holds the rebuilt session ($(echo $panes))" \
    || bad "[$m] rebuilt session" "$panes"
  out="$d/state/rr_respawn_from_${SRC[$m]}.out"
  if [[ "$m" == early && "$(start_of "${SUCC[$m]}")" > "$(end_of "${SRC[$m]}")" && "$(end_of "${SRC[$m]}")" != Unknown ]]; then
    echo "  NOT EXERCISED  [$m] handoff: the successor started after the source had ended (queue wait); the stub tests cover the handoff"
    grep -q "requesting a handoff" "$out" && bad "[$m] no handoff expected" "$(grep handoff "$out")" || ok "[$m] no handoff (source had ended)"
  elif [[ "$m" == early ]]; then
    grep -q "took the final snapshot" "$out" && ok "[$m] handoff answered by the source coordinator" || bad "[$m] handoff" "$(grep -i handoff "$out")"
    grep -q "scancelled source job ${SRC[$m]}" "$out" && ok "[$m] successor ended the source" || bad "[$m] scancel" "$(tail -5 "$out")"
  else
    grep -q "requesting a handoff" "$out" && bad "[$m] no handoff expected" "$(grep handoff "$out")" || ok "[$m] no handoff (source had ended)"
  fi
  [[ ! -s "$d/state/registry/${SUCC[$m]}/hop.json" ]] && bad "[$m] registration carried" "missing" \
    || { [[ "$(jq -r .permission_mode "$d/state/registry/${SUCC[$m]}/hop.json")" == acceptEdits ]] && ok "[$m] registration carried with its settings" || bad "[$m] carried settings" "$(cat "$d/state/registry/${SUCC[$m]}/hop.json")"; }
done

echo "== cleanup: cancelling only the jobs this test submitted =="
for j in "${SUBMITTED[@]}"; do scancel "$j" 2>/dev/null; done
sleep 5
printf '%s\n' "${SUBMITTED[@]}" > "$WD/jobs.txt"
sacct -X -j "$(IFS=,; echo "${SUBMITTED[*]}")" -o JobID,JobName%18,Partition,State%14,Elapsed,AllocCPUS,NodeList 2>/dev/null | tee "$WD/sacct.txt"
echo; echo "=== $pass passed, $fail failed ==="
[[ $fail -eq 0 ]]
