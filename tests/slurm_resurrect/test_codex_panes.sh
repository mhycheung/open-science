#!/bin/bash
# Codex panes across a hop: detection, snapshot, rebuild, notes, and the user-only gate.
#
#   bash tests/slurm_resurrect/test_codex_panes.sh
#
# A fake `codex` (a bash script, so its process name is `codex`) runs in the panes of a
# private tmux server; `codex queue` only logs. The open-science Codex hook's pane
# record (<core state>/codex/panes/<key>.json) is written by hand, as cx_hook.sh would.
# Every check has a case it must refuse:
#   * a Codex pane with a hook record is resumed with its own options and thread; one
#     with no record, or with an option the scripts do not know, is left a shell;
#   * nothing that widens permissions is added, and the Claude permission mode is not
#     applied to Codex;
#   * a note for a Codex pane goes through `codex queue`, never typed;
#   * a Codex folder-trust question is never answered;
#   * a Codex agent (CODEX_THREAD_ID, or a `codex` ancestor) cannot register.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
rr_test_setup
RR="$SCRIPTS/rr_registry.sh"
job 424242 RUNNING 1:00:00 2:00:00
export FAKE_CX_QUEUE_LOG="$TMP/queue.log"; : > "$FAKE_CX_QUEUE_LOG"
cat > "$TMP/bin/codex" <<'EOF'
#!/bin/bash
if [[ "${1:-}" == queue ]]; then
  shift; t=""; m=""
  while [[ $# -gt 0 ]]; do case "$1" in --thread) t="$2"; shift 2 ;; --message) m="$2"; shift 2 ;; *) shift ;; esac; done
  printf '%s\t%s\n' "$t" "$m" >> "$FAKE_CX_QUEUE_LOG"; exit 0
fi
while :; do sleep 1; done
EOF
chmod +x "$TMP/bin/codex"
export RR_CODEX_LAUNCH_CMD="$TMP/bin/codex"

start_tmux work
tmux -S "$SOCK" split-window -t work:0 >/dev/null
tmux -S "$SOCK" split-window -t work:0 >/dev/null
tmux -S "$SOCK" select-layout -t work:0 tiled >/dev/null
key() { printf '%s__%s' "$(printf '%s' "$SOCK" | tr -c 'A-Za-z0-9._-' '_')" "$(printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_')"; }

# start_codex <target> <args...> -> pid of the fake TUI
start_codex() {
  local target="$1" ppid pid i; shift
  ppid=$(tmux -S "$SOCK" display-message -p -t "$target" '#{pane_pid}')
  tmux -S "$SOCK" send-keys -t "$target" "CODEX_HOME=$TMP/cxhome FAKE_CX_QUEUE_LOG=$FAKE_CX_QUEUE_LOG $TMP/bin/codex $*" Enter
  for ((i=0; i<50; i++)); do
    pid=$(pgrep -P "$ppid" -x codex | head -1); [[ -n "$pid" ]] && break; sleep 0.2
  done
  echo "$pid"
}
record() {  # <target> <pid> <thread> <status>
  local paneid; paneid=$(tmux -S "$SOCK" display-message -p -t "$1" '#{pane_id}')
  mkdir -p "$OPSCI_STATE_DIR/codex/panes"
  jq -n --arg p "$2" --arg t "$3" --arg s "$4" \
    '{runtime:"codex", thread_id:$t, tui_pid:$p, status:$s, model:"gpt-test", codex_home:""}' \
    > "$OPSCI_STATE_DIR/codex/panes/$(key "$paneid").json"
}

P0=$(start_codex work:0.0 -s workspace-write -a on-request -c "'x=\"a b\"'" "'old prompt'")
P1=$(start_codex work:0.1 -s read-only)
P2=$(start_codex work:0.2 --some-new-flag)
record work:0.0 "$P0" thread-0 busy
record work:0.2 "$P2" thread-2 idle
# (pane 1 has no hook record)

echo "== 1. snapshot =="
SNAP="$TMP/snap.json"
RR_SELFREG_DIR="$TMP/panes" bash "$SCRIPTS/rr_snapshot.sh" "$SOCK" work "$SNAP" 2>/dev/null
pane() { jq -c ".windows[0].panes[] | select(.index == $1)" "$SNAP"; }
check "pane 0 is a resumable Codex pane" "$(pane 0 | jq -r '[.runtime, .codex, .session_id] | join(",")')" "codex,true,thread-0"
check "pane 0 keeps the TUI's own options" "$(pane 0 | jq -c .codex_options)" '["-s","workspace-write","-a","on-request","-c","x=\"a b\""]'
check "pane 0 model and CODEX_HOME" "$(pane 0 | jq -r '[.model, .codex_home] | join(",")')" "gpt-test,$TMP/cxhome"
check "pane 1 (no hook record) is not resumable" "$(pane 1 | jq -r .codex)" "false"
has   "pane 1 says why" "$(pane 1 | jq -r .codex_unresumable)" "cx_hook.sh"
check "pane 2 (unknown option) is not resumable" "$(pane 2 | jq -r .codex)" "false"
has   "pane 2 says why" "$(pane 2 | jq -r .codex_unresumable)" "--some-new-flag"
check "the pane cache now knows thread-0" "$(jq -r .session_id "$TMP/panes/$(tmux -S "$SOCK" display-message -p -t work:0.0 '#{pane_id}' | tr -d '\n' | tr -c 'A-Za-z0-9._-' '_').json")" "thread-0"

echo "== 2. rebuild (dry run) on a new server =="
SOCK2="$TMP/tmux2.sock"
REC="$TMP/rec.json"; echo '{"permission_mode":"plan","remote_control":true}' > "$REC"
man=$(RR_REBUILD_DRYRUN=1 RR_WRITE_SELFREG_DIR="$TMP/panes2" bash "$SCRIPTS/rr_rebuild.sh" "$SNAP" "$SOCK2" "$REC" 2>"$TMP/rebuild.err")
l0=$(jq -c 'select(.target | endswith(".0"))' <<<"$man")
launch=$(jq -r .launch <<<"$l0")
has   "pane 0 resumes its thread" "$launch" "resume"
has   "pane 0 resumes thread-0 last" "$launch" " thread-0"
has   "pane 0 keeps its sandbox and approval policy" "$launch" "-s workspace-write -a on-request"
has   "pane 0 keeps its config override" "$launch" 'x=\"a\ b\"'
has   "pane 0 passes the model" "$launch" "-m gpt-test"
has   "pane 0 restores CODEX_HOME" "$launch" "CODEX_HOME=$TMP/cxhome"
hasnt "the Claude permission mode is not applied" "$launch" "plan"
hasnt "no bypass flag is added" "$launch" "dangerously"
hasnt "the old prompt is not replayed" "$launch" "old"
check "pane 1 is reported unresumable" "$(jq -r 'select(.target | endswith(".1")) | .status' <<<"$man")" "unresumable"
check "pane 2 is reported unresumable" "$(jq -r 'select(.target | endswith(".2")) | .status' <<<"$man")" "unresumable"
sleep 1
has   "the command was typed into the shell (commented in dry run)" "$(tmux -S "$SOCK2" capture-pane -p -t work:0.0)" "# CODEX_HOME"
newid=$(tmux -S "$SOCK2" display-message -p -t work:0.0 '#{pane_id}' | tr -d '\n' | tr -c 'A-Za-z0-9._-' '_')
check "the next hop's pane cache is seeded" "$(jq -r '[.runtime, .session_id] | join(",")' "$TMP/panes2/$newid.json")" "codex,thread-0"
tmux -S "$SOCK2" kill-server 2>/dev/null

echo "== 2b. a reused pid does not inherit a thread =="
P0START=$(sed 's/.*) //' /proc/$P0/stat | awk '{print $20}')
paneid0=$(tmux -S "$SOCK" display-message -p -t work:0.0 '#{pane_id}')
REC0="$OPSCI_STATE_DIR/codex/panes/$(key "$paneid0").json"
jq '.tui_start="1"' "$REC0" > "$REC0.t" && mv "$REC0.t" "$REC0"
RR_SELFREG_DIR="$TMP/panes_x" bash "$SCRIPTS/rr_snapshot.sh" "$SOCK" work "$TMP/snap_x.json" 2>/dev/null
check "record with another start time -> thread unknown" \
  "$(jq -r '.windows[0].panes[] | select(.index == 0) | .codex' "$TMP/snap_x.json")" "false"
jq --arg s "$P0START" '.tui_start=$s' "$REC0" > "$REC0.t" && mv "$REC0.t" "$REC0"
RR_SELFREG_DIR="$TMP/panes_x" bash "$SCRIPTS/rr_snapshot.sh" "$SOCK" work "$TMP/snap_x.json" 2>/dev/null
check "record with the matching start time -> resumable" \
  "$(jq -r '.windows[0].panes[] | select(.index == 0) | .session_id' "$TMP/snap_x.json")" "thread-0"

echo "== 2c. wind-down: the busy Codex pane is told through codex queue, the idle one is not =="
mkdir -p "$RR_STATE_DIR/registry/424242"; touch "$RR_STATE_DIR/warning_shown"
jq -n --arg s "$SOCK" '{tmux_session:"work", tmux_socket:$s, sanitized:"work", permission_mode:"bypassPermissions", remote_control:true}' \
  > "$RR_STATE_DIR/registry/424242/work.json"
job 424242 RUNNING 0:20:00 2:00:00
: > "$FAKE_CX_QUEUE_LOG"
RR_COORD_INTERVAL=1 bash "$SCRIPTS/rr_coordinator.sh" 424242 >/dev/null 2>&1 & COORD=$!
for ((i=0; i<100; i++)); do [[ -s "$FAKE_CX_QUEUE_LOG" ]] && break; sleep 0.2; done
sleep 2; kill "$COORD" 2>/dev/null; wait "$COORD" 2>/dev/null
q=$(cat "$FAKE_CX_QUEUE_LOG")
has   "busy Codex pane (thread-0) got the wind-down message" "$q" $'thread-0\t[slurm-resurrect] This SLURM job is ~'
hasnt "idle Codex pane (thread-2) was not disturbed" "$q" "thread-2"
check "one message only" "$(grep -c . "$FAKE_CX_QUEUE_LOG")" "1"
has   "logged as sent through codex queue" "$(cat "$RR_STATE_DIR/coordinator_424242.log")" "wind-down nudge -> codex thread thread-0"
check "the Codex panes' jumps are inhibited" "$(grep -c . "$RR_STATE_DIR/inhibit_424242.list")" "3"
rm -rf "$RR_STATE_DIR/registry/424242" "$RR_STATE_DIR/inhibit_424242.list" "$RR_STATE_DIR/winddown_424242"
rm -f "$OPSCI_STATE_DIR"/inhibit_jump_*
job 424242 RUNNING 1:00:00 2:00:00
: > "$FAKE_CX_QUEUE_LOG"

echo "== 3. deliver: notes by codex queue, trust question left alone =="
tmux -S "$SOCK" send-keys -t work:0.1 C-c; sleep 0.5
tmux -S "$SOCK" send-keys -t work:0.1 "clear; echo 'Trust this folder?'; echo '> 1. Trust and continue'" Enter; sleep 0.5
MAN="$TMP/man.jsonl"
jq -nc --arg s "$SOCK" '{socket:$s, target:"work:0.0", runtime:"codex", status:"busy", session_id:"thread-0", codex_home:"", note:"carry on from step 3", note_src:"", jump:null}' > "$MAN"
jq -nc --arg s "$SOCK" '{socket:$s, target:"work:0.1", runtime:"codex", status:"idle", session_id:"thread-1", codex_home:"", note:"", note_src:"", jump:null}' >> "$MAN"
before=$(tmux -S "$SOCK" capture-pane -p -t work:0.1)
out=$(RR_BOOT_WAIT=0 RR_SETTLE_WAIT=0 bash "$SCRIPTS/rr_deliver.sh" "$MAN" 2>&1)
check "the note was queued to thread-0" "$(cat "$FAKE_CX_QUEUE_LOG")" $'thread-0\tcarry on from step 3'
has   "the trust question is left for the user" "$out" "left for the user"
check "nothing was typed into the trust pane" "$(tmux -S "$SOCK" capture-pane -p -t work:0.1)" "$before"
has   "Remote Control is reported as not applying" "$out" "Remote Control does not apply"

echo "== 4. a Codex agent cannot register =="
PANE=$(tmux -S "$SOCK" display-message -p -t work:0.0 '#{pane_id}')
CODEX_THREAD_ID=t1 TMUX="$SOCK,1,0" TMUX_PANE="$PANE" bash "$RR" register >/dev/null 2>&1
check "CODEX_THREAD_ID set -> refused (exit 3)" "$?" "3"
cp "$(command -v bash)" "$TMP/bin/as/codex"
tmux -S "$SOCK" send-keys -t work:0.2 C-c; sleep 0.5
out=$(in_pane work:0.2 "$TMP/bin/as/codex -c 'TMUX=$SOCK,1,0 TMUX_PANE=$PANE bash $RR register; exit \$?'")
check "codex ancestor -> refused (exit 3)" "$(pane_rc)" "3"
[[ ! -e "$RR_STATE_DIR/registry/424242/work.json" ]] && ok "nothing registered" || bad "nothing registered" "registry exists"

finish
