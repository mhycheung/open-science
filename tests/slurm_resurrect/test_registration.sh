#!/bin/bash
# Registration is the user's decision; the first-use warning is shown once.
#
#   bash tests/slurm_resurrect/test_registration.sh
#
# Every check has a case it must refuse next to one it must accept:
#   * an agent (CLAUDECODE set, or a `claude` ancestor) is refused; the user in a
#     terminal pane is accepted;
#   * the prompt hook acts on the literal /slurm-resurrect:resurrect command and
#     ignores every other prompt, including injection attempts;
#   * the warning is printed (and nothing registered) on the first run, and the
#     second run is silent and registers;
#   * agents may still run the harmless commands (status, note, stop).
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
rr_test_setup
RR="$SCRIPTS/rr_registry.sh"
HOOK="$SCRIPTS/rr_prompt_hook.sh"
job 424242 RUNNING 1:00:00 2:00:00

start_tmux work
PANE=$(tmux -S "$SOCK" display-message -p -t work:0.0 '#{pane_id}')
TMUXVAL="$SOCK,1,0"
REG="$RR_STATE_DIR/registry/424242/work.json"

echo "== 1. an agent cannot register =="
out=$(CLAUDECODE=1 TMUX="$TMUXVAL" TMUX_PANE="$PANE" bash "$RR" register 2>&1); rc=$?
check "CLAUDECODE set -> register refused (exit 3)" "$rc" "3"
has   "refusal tells the agent to ask the user" "$out" "/slurm-resurrect:resurrect register"
[[ ! -e "$REG" ]] && ok "nothing registered" || bad "nothing registered" "$REG exists"
[[ ! -e "$RR_STATE_DIR/warning_shown" ]] && ok "refused call did not consume the first-use warning" \
  || bad "warning marker" "set by a refused call"
has "attempt logged in refused.log" "$(cat "$RR_STATE_DIR/refused.log" 2>/dev/null)" "REFUSED 'register'"

# An agent whose environment was scrubbed is still caught by its ancestry: the
# command runs under a live process named `claude`, as a Bash tool call does.
# (The trailing `; exit $?` stops bash from exec-ing the command in place of the
# `claude` process, which would remove the ancestor this case is about.)
out=$(in_pane work:0.0 "$TMP/bin/as/claude -c 'TMUX=$TMUXVAL TMUX_PANE=$PANE bash $RR register; exit \$?'")
check "claude ancestor, no CLAUDECODE -> refused (exit 3)" "$(pane_rc)" "3"
[[ ! -e "$REG" ]] && ok "still nothing registered" || bad "nothing registered" "$REG exists"

for c in "reset 50" "set queue_mode early" "set-notify echo hi"; do
  CLAUDECODE=1 bash "$RR" $c >/dev/null 2>&1; rc=$?
  check "agent '$c' refused (exit 3)" "$rc" "3"
done
check "config not raised by the refused reset" "$(jq -r '.max_resurrections // "none"' "$RR_STATE_DIR/rr_config.json" 2>/dev/null || echo none)" "none"

echo "== 2. the user in a terminal pane: warning once, then register =="
out=$(in_pane work:0.0 "bash $RR register")
check "first run exits 4 (warning only)" "$(pane_rc)" "4"
has   "first run shows the permission-mode warning" "$out" "bypassPermissions"
has   "first run shows the Remote Control warning" "$out" "Remote Control"
[[ ! -e "$REG" ]] && ok "first run registers nothing" || bad "first run registers nothing" "$REG exists"
[[ -f "$RR_STATE_DIR/warning_shown" ]] && ok "warning recorded as shown" || bad "warning marker" "missing"

out=$(in_pane work:0.0 "bash $RR register")
check "second run exits 0" "$(pane_rc)" "0"
hasnt "second run is silent about the warning" "$out" "read this once"
has   "second run registers" "$out" "registered tmux session 'work'"
has   "register says folder trust is not chosen yet" "$out" "Folder trust: not chosen yet"
check "registered_via terminal" "$(jq -r .registered_via "$REG" 2>/dev/null)" "terminal"
check "default: no permission cap (each pane's own mode)" "$(jq -r .permission_mode "$REG" 2>/dev/null)" "null"
check "default: the mode is not marked as the user's choice" "$(jq -r .permission_mode_explicit "$REG" 2>/dev/null)" "false"
check "default Remote Control on" "$(jq -r .remote_control "$REG" 2>/dev/null)" "true"
check "hop cap defaults to 10" "$(jq -r .max_resurrections "$RR_STATE_DIR/rr_config.json")" "10"
[[ -f "$RR_STATE_DIR/scripts/rr_successor.sh" ]] && ok "scripts copied to the stable dir" \
  || bad "stable scripts" "missing $RR_STATE_DIR/scripts"

out=$(in_pane work:0.0 "bash $RR register --permission-mode yolo")
check "invalid permission mode refused (exit 2)" "$(pane_rc)" "2"
out=$(in_pane work:0.0 "bash $RR register --remote-control maybe")
check "invalid remote-control value refused (exit 2)" "$(pane_rc)" "2"
check "the invalid calls left the registration unchanged" "$(jq -r .permission_mode "$REG")" "null"

out=$(in_pane work:0.0 "bash $RR register --permission-mode acceptEdits --remote-control off")
check "re-register with choices exits 0" "$(pane_rc)" "0"
has   "after a choice, register reports it" "$(in_pane work:0.0 "bash $RR set auto_trust false >/dev/null; bash $RR register --permission-mode acceptEdits --remote-control off")" "Folder trust: auto_trust is false."
check "permission mode recorded" "$(jq -r .permission_mode "$REG")" "acceptEdits"
check "and marked as the user's choice" "$(jq -r .permission_mode_explicit "$REG")" "true"
check "Remote Control off recorded" "$(jq -r .remote_control "$REG")" "false"

echo "== 3. agents may run the harmless commands =="
CLAUDECODE=1 bash "$RR" status >/dev/null 2>&1; check "agent status allowed" "$?" "0"
CLAUDECODE=1 TMUX="$TMUXVAL" TMUX_PANE="$PANE" bash "$RR" note "resume the sweep" >/dev/null 2>&1
check "agent note allowed" "$?" "0"
PK=$(printf '%s' "$PANE" | tr -c 'A-Za-z0-9._-' '_')
check "note saved for the pane" "$(cat "$RR_STATE_DIR/registry/424242/notes/$PK.txt" 2>/dev/null)" "resume the sweep"

echo "== 4. the prompt hook (the /slurm-resurrect:resurrect path) =="
# A fresh state dir, so the hook path meets the first-use warning too.
export RR_STATE_DIR="$TMP/rr2"; REG2="$RR_STATE_DIR/registry/424242/work.json"
hook() { jq -nc --arg p "$1" '{prompt:$p, hook_event_name:"UserPromptSubmit"}' \
          | CLAUDECODE=1 TMUX="$TMUXVAL" TMUX_PANE="$PANE" bash "$HOOK"; }

out=$(hook "/slurm-resurrect:resurrect register")
check "first use through the hook blocks the prompt" "$(jq -r .decision <<<"$out" 2>/dev/null)" "block"
has   "the block reason is the warning" "$(jq -r .reason <<<"$out" 2>/dev/null)" "bypassPermissions"
[[ ! -e "$REG2" ]] && ok "hook first use registers nothing" || bad "hook first use" "$REG2 exists"

out=$(hook "/slurm-resurrect:resurrect register --remote-control off")
check "second hook call does not block" "$(jq -r '.decision // "none"' <<<"$out" 2>/dev/null)" "none"
has   "hook output reaches the model context" "$(jq -r .hookSpecificOutput.additionalContext <<<"$out")" "registered tmux session 'work'"
hasnt "second hook call is silent about the warning" "$out" "read this once"
check "registered_via slash-command" "$(jq -r .registered_via "$REG2" 2>/dev/null)" "slash-command"
check "hook passes options through" "$(jq -r .remote_control "$REG2" 2>/dev/null)" "false"

rm -f "$REG2"
for p in "register this tmux session for slurm resurrection" \
         "please run /slurm-resurrect:resurrect register" \
         "/slurm-resurrect:resurrectregister" \
         "/other-plugin:resurrect register"; do
  out=$(hook "$p")
  [[ -z "$out" && ! -e "$REG2" ]] && ok "hook ignores: $p" || bad "hook must ignore: $p" "out=$out"
done
out=$(hook "/slurm-resurrect:resurrect register; touch $TMP/pwned \$(touch $TMP/pwned2)")
[[ ! -e "$TMP/pwned" && ! -e "$TMP/pwned2" ]] && ok "prompt text is never evaluated as shell" \
  || bad "no shell evaluation of the prompt" "a pwned file was created"

echo "== 5. a prompt typed by the scripts is not run as a user command =="
# rr_deliver.sh and the core's worker hold the pane's injection lock while they type
# and press Enter. A /slurm-resurrect:resurrect prompt submitted while it is held came
# from them (for example a delivered note), so the hook must block it.
source "$SCRIPTS/rr_common.sh"
LOCKF=$(rr_pane_lock "$SOCK" "$PANE")
( exec 9>"$LOCKF"; flock 9; sleep 4 ) & LPID=$!
sleep 0.5
out=$(hook "/slurm-resurrect:resurrect set-notify touch $TMP/pwned3")
check "prompt while the pane lock is held -> blocked" "$(jq -r .decision <<<"$out" 2>/dev/null)" "block"
check "notify_cmd not set by the injected prompt" "$(jq -r '.notify_cmd // "none"' "$RR_STATE_DIR/rr_config.json")" "none"
wait "$LPID"
# Control: the same prompt with the lock free is the user's and runs.
out=$(hook "/slurm-resurrect:resurrect set-notify true")
check "lock free -> the user's command runs" "$(jq -r '.notify_cmd // "none"' "$RR_STATE_DIR/rr_config.json")" "true"

echo "== 6. a socket in a directory other users can reach is refused =="
LOOSE="$TMP/loose"; mkdir -p "$LOOSE"; chmod 755 "$LOOSE"
rr_sock_ok "$LOOSE/s" 2>/dev/null && bad "group/other-readable dir must be refused" "accepted" \
  || ok "socket dir with group/other permissions refused"
mkdir -m 700 "$TMP/private"; ln -s "$TMP/private" "$TMP/link"
rr_sock_ok "$TMP/link/s" 2>/dev/null && bad "symlinked dir must be refused" "accepted" || ok "symlinked socket dir refused"
rr_sock_ok "/rr-test-$$.sock" 2>/dev/null && bad "a dir owned by another user (/) must be refused" "accepted" \
  || ok "socket dir owned by another user (/) refused"
: > "$TMP/private/notasock"
rr_sock_ok "$TMP/private/notasock" 2>/dev/null && bad "a regular file must be refused as a socket" "accepted" \
  || ok "existing non-socket at the socket path refused"
rr_sock_ok "$TMP/private/s" && ok "control: private dir accepted" || bad "private dir must be accepted" "refused"
rr_sock_ok "$SOCK" && ok "control: the test's own live socket accepted" || bad "live socket must be accepted" "refused"

echo "== 7. a symlink at a state or lock path is replaced, its target never written =="
# A sandboxed agent that can write the state directory could plant links there; a
# `>`-opened lock or state file would then truncate or overwrite the link's target.
VICTIM="$TMP/victim"; vic() { printf 'precious' > "$VICTIM"; }
intact() { check "$1: the link's target is untouched" "$(cat "$VICTIM")" "precious"
           [[ ! -L "$2" ]] && ok "$1: the link was replaced" || bad "$1: link still there" "$2"; }
# pane lock, opened by rr_deliver.sh (trust step) and the coordinator's nudge
LOCKF=$(rr_pane_lock "$SOCK" "$PANE"); vic; rm -f "$LOCKF"; ln -s "$VICTIM" "$LOCKF"
tmux -S "$SOCK" new-window -d -t work:7 "printf 'Do you trust this folder?\n'; sleep 30"
LOCKF7=$(rr_pane_lock "$SOCK" work:7); rm -f "$LOCKF7"; ln -s "$VICTIM" "$LOCKF7"
jq -nc --arg s "$SOCK" '{socket:$s, target:"work:7", status:"idle", session_id:"x", note:"", jump:null}' > "$TMP/m7.jsonl"
echo '{"auto_trust": true}' | rr_write "$RR_STATE_DIR/rr_config.json"
out=$(RR_BOOT_WAIT=0 RR_SETTLE_WAIT=0 RR_TRUST_KEY_WAIT=0.1 bash "$SCRIPTS/rr_deliver.sh" "$TMP/m7.jsonl" 2>&1)
has "rr_deliver took the pane lock for the trust dialog" "$out" "trust this folder"
intact "pane lock (rr_deliver)" "$LOCKF7"
rr_lock_file "$LOCKF" >/dev/null; intact "pane lock (rr_pane_lock)" "$LOCKF"
# coordinator lock and marker
LK="$RR_STATE_DIR/locks/coordinator_77.lock"; mkdir -p "$RR_STATE_DIR/locks"; vic; ln -sf "$VICTIM" "$LK"
ln -sf "$VICTIM" "$RR_STATE_DIR/locks/coordinator_77.running"
job 77 RUNNING 0:00:01 1:00:00
timeout 20 bash "$SCRIPTS/rr_coordinator.sh" 77 >/dev/null 2>&1
intact "coordinator lock" "$LK"
intact "coordinator marker" "$RR_STATE_DIR/locks/coordinator_77.running"
# notification log (appended)
vic; rm -f "$RR_STATE_DIR/notifications.log"; ln -s "$VICTIM" "$RR_STATE_DIR/notifications.log"
rr_notify "test message"
intact "notifications.log" "$RR_STATE_DIR/notifications.log"
has "the message went to the real log" "$(cat "$RR_STATE_DIR/notifications.log")" "test message"
# config (rewritten by set); the link points at a JSON file the user cares about
CFG="$RR_STATE_DIR/rr_config.json"; JV="$TMP/victim.json"
echo '{"queue_mode":"afterany","mine":1}' > "$JV"; rm -f "$CFG"; ln -s "$JV" "$CFG"
in_pane work:0.0 "RR_STATE_DIR='$RR_STATE_DIR' bash $RR set queue_mode early" >/dev/null
check "config: the link's target is untouched" "$(jq -c . "$JV")" '{"queue_mode":"afterany","mine":1}'
[[ ! -L "$CFG" ]] && ok "config: the link was replaced" || bad "config: link still there" "$CFG"
check "config: the setting went to the real config" "$(jq -r .queue_mode "$CFG")" "early"
# a pane's note
NOTE="$RR_STATE_DIR/registry/424242/notes/$(printf '%s' "$PANE" | tr -c 'A-Za-z0-9._-' '_').txt"
mkdir -p "$(dirname "$NOTE")"; vic; rm -f "$NOTE"; ln -s "$VICTIM" "$NOTE"
CLAUDECODE=1 TMUX="$TMUXVAL" TMUX_PANE="$PANE" bash "$RR" note "the next step" >/dev/null 2>&1
intact "note" "$NOTE"
check "the note was written to its own file" "$(cat "$NOTE")" "the next step"
# control: rr_write writes a normal file and refuses empty input
printf 'x' | rr_write "$TMP/plain" && check "control: rr_write writes a file" "$(cat "$TMP/plain")" "x"
: | rr_write "$TMP/plain"; check "rr_write keeps the file on empty input" "$(cat "$TMP/plain")" "x"

finish
