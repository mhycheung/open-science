#!/bin/bash
# Shared setup for the slurm-resurrect shell tests. Source it, then call rr_test_setup.
#
# Everything is private to one temp dir: a tmux server on its own socket file,
# RR_STATE_DIR, a fake Claude config dir, and stub squeue/sbatch/scancel on PATH,
# so no test touches a real scheduler, a real Claude session, the user's tmux
# server or the user's resurrection state. Runs without SLURM or Claude (CI).
#
# Stub scheduler: a job is a file $STUB/jobs/<id> holding one line
#   STATE LEFT TOTAL ACCOUNT PARTITION NODES
# squeue answers from those files; sbatch records its arguments in
# $STUB/sbatch.log, creates a PENDING job and prints its id; scancel records its
# arguments in $STUB/scancel.log and removes the job.
set -u

T_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN="$(cd "$T_HERE/../../extras/slurm-resurrect" && pwd)"
SCRIPTS="$PLUGIN/scripts"

pass=0; fail=0
ok()   { echo "  PASS  $1"; pass=$((pass+1)); }
bad()  { echo "  FAIL  $1"; echo "        $2"; fail=$((fail+1)); }
check(){ [[ "$2" == "$3" ]] && ok "$1" || bad "$1" "expected '$3', got '$2'"; }
has()  { [[ "$2" == *"$3"* ]] && ok "$1" || bad "$1" "'$3' not found in: $2"; }
hasnt(){ [[ "$2" != *"$3"* ]] && ok "$1" || bad "$1" "'$3' must not appear in: $2"; }
finish() { echo; echo "=== $pass passed, $fail failed ==="; [[ $fail -eq 0 ]]; }

rr_test_cleanup() {
  tmux -S "$SOCK" kill-server 2>/dev/null
  [[ -n "${TMP:-}" ]] && pkill -f "$TMP/" 2>/dev/null
  rm -rf "$TMP"
}

rr_test_setup() {
  # Never inherit the environment of a session that runs the tests: an outer
  # Claude session (CLAUDECODE), a tmux client, or a real SLURM job.
  unset CLAUDECODE TMUX TMUX_PANE RR_CALLER CLAUDE_CODE_SESSION_ID SLURM_JOB_ACCOUNT \
        SLURM_JOB_PARTITION SLURM_NTASKS SLURM_CPUS_PER_TASK SLURM_JOB_NUM_NODES \
        RR_CORE_SCRIPTS_DIR OPSCI_STATE_DIR
  TMP=$(mktemp -d "${TMPDIR:-/tmp}/rrtest.XXXXXX"); trap rr_test_cleanup EXIT
  SOCK="$TMP/tmux.sock"
  STUB="$TMP/stub"
  export RR_STATE_DIR="$TMP/rr"
  export OPSCI_STATE_DIR="$TMP/os"
  export RR_CONFIG_DIRS="$TMP/cfg"
  export SLURM_JOB_ID="424242"
  export RR_NO_COORDINATOR=1
  export XDG_STATE_HOME="$TMP/xdg"
  mkdir -p "$TMP/bin" "$TMP/cfg/sessions" "$TMP/cfg/projects/p" "$STUB/jobs" "$RR_STATE_DIR"
  export STUB
  cat > "$STUB/squeue" <<'SQ'
#!/bin/bash
job=""; fmt="%i"
while [[ $# -gt 0 ]]; do
  case "$1" in -j) job="$2"; shift 2 ;; -o) fmt="$2"; shift 2 ;; -u) shift 2 ;; *) shift ;; esac
done
emit() {
  local id="$1" st left tot acct part nodes out
  read -r st left tot acct part nodes < "$STUB/jobs/$id"
  out="$fmt"
  out="${out//%i/$id}"; out="${out//%T/$st}"; out="${out//%L/$left}"; out="${out//%l/$tot}"
  out="${out//%a/$acct}"; out="${out//%P/$part}"; out="${out//%D/$nodes}"
  echo "$out"
}
if [[ -n "$job" ]]; then [[ -f "$STUB/jobs/$job" ]] && emit "$job"
else for f in "$STUB"/jobs/*; do [[ -f "$f" ]] && emit "$(basename "$f")"; done; fi
exit 0
SQ
  cat > "$STUB/sbatch" <<'SB'
#!/bin/bash
echo "$*" >> "$STUB/sbatch.log"
n=$(cat "$STUB/next_id" 2>/dev/null || echo 900001)
echo $((n+1)) > "$STUB/next_id"
echo "PENDING 1:00:00 1:00:00 acct part 1" > "$STUB/jobs/$n"
echo "$n"
SB
  cat > "$STUB/scancel" <<'SC'
#!/bin/bash
echo "$*" >> "$STUB/scancel.log"
for a in "$@"; do rm -f "$STUB/jobs/$a"; done
exit 0
SC
  chmod +x "$STUB"/squeue "$STUB"/sbatch "$STUB"/scancel
  export PATH="$STUB:$PATH"
  [[ "$(command -v sbatch)" == "$STUB/sbatch" ]] || { echo "stub sbatch not first on PATH; refusing to run"; exit 1; }
  # A process genuinely named `claude` (a copy of sleep), so the liveness gate is
  # exercised for real rather than stubbed.
  cp "$(command -v sleep)" "$TMP/bin/claude"
  # A shell named `claude`: an ancestor with that name marks its children as agents.
  cp "$(command -v bash)" "$TMP/bin/claudeshell"; mkdir -p "$TMP/bin/as"; cp "$(command -v bash)" "$TMP/bin/as/claude"
}

# job <id> <STATE> [LEFT] [TOTAL]
job() { echo "$2 ${3:-1:00:00} ${4:-2:00:00} testacct testpart 1" > "$STUB/jobs/$1"; }

# Start the private tmux server with one session. Its panes are "user terminals":
# the server daemonizes, so no pytest (or Claude) process is their ancestor.
start_tmux() {  # <session>
  env -u CLAUDECODE tmux -S "$SOCK" -f /dev/null new-session -d -s "$1" -x 200 -y 50
  tmux -S "$SOCK" set-option -g remain-on-exit off >/dev/null
}

# Run a command in a pane as if the user typed it, wait for it to finish, and
# print its combined output; the exit status goes to $TMP/pane.rc.
in_pane() {  # <target> <command...>
  local target="$1"; shift
  local id=$RANDOM$RANDOM out="$TMP/pane.$RANDOM.out"
  rm -f "$TMP/pane.rc"
  tmux -S "$SOCK" send-keys -t "$target" \
    "export RR_STATE_DIR='$RR_STATE_DIR' OPSCI_STATE_DIR='$OPSCI_STATE_DIR' SLURM_JOB_ID='$SLURM_JOB_ID' RR_NO_COORDINATOR=1 PATH='$PATH' RR_CONFIG_DIRS='$RR_CONFIG_DIRS' STUB='$STUB'; unset CLAUDECODE; ( $* ) > '$out' 2>&1; echo \$? > '$TMP/pane.rc.$id'" Enter
  local i; for ((i=0; i<150; i++)); do [[ -f "$TMP/pane.rc.$id" ]] && break; sleep 0.2; done
  mv "$TMP/pane.rc.$id" "$TMP/pane.rc" 2>/dev/null
  cat "$out" 2>/dev/null
}
pane_rc() { cat "$TMP/pane.rc" 2>/dev/null || echo none; }

# Start a fake Claude in a pane: a process named `claude` (exec'd, so it keeps
# the pane's pid), CLAUDE_CONFIG_DIR in its environment, a sessions/<pid>.json
# state file and a transcript naming the model. Prints the pid.
fake_claude() {  # <target> <session_id> [model] [status]
  local target="$1" sid="$2" model="${3:-claude-test-model}" status="${4:-idle}" pid i
  pid=$(tmux -S "$SOCK" display-message -p -t "$target" '#{pane_pid}')
  tmux -S "$SOCK" send-keys -t "$target" "CLAUDE_CONFIG_DIR=$TMP/cfg exec $TMP/bin/claude 3600" Enter
  for ((i=0; i<50; i++)); do
    [[ "$(ps -o comm= -p "$pid" 2>/dev/null)" == claude ]] && break; sleep 0.2
  done
  jq -n --arg s "$sid" --arg st "$status" '{sessionId:$s, status:$st}' > "$TMP/cfg/sessions/$pid.json"
  jq -nc --arg m "$model" '{type:"assistant", isSidechain:false, message:{model:$m}}' \
    > "$TMP/cfg/projects/p/$sid.jsonl"
  echo "$pid"
}
