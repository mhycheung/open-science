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
check "registered_via terminal" "$(jq -r .registered_via "$REG" 2>/dev/null)" "terminal"
check "default permission mode" "$(jq -r .permission_mode "$REG" 2>/dev/null)" "bypassPermissions"
check "default Remote Control on" "$(jq -r .remote_control "$REG" 2>/dev/null)" "true"
check "hop cap defaults to 10" "$(jq -r .max_resurrections "$RR_STATE_DIR/rr_config.json")" "10"
[[ -f "$RR_STATE_DIR/scripts/rr_successor.sh" ]] && ok "scripts copied to the stable dir" \
  || bad "stable scripts" "missing $RR_STATE_DIR/scripts"

out=$(in_pane work:0.0 "bash $RR register --permission-mode yolo")
check "invalid permission mode refused (exit 2)" "$(pane_rc)" "2"
out=$(in_pane work:0.0 "bash $RR register --remote-control maybe")
check "invalid remote-control value refused (exit 2)" "$(pane_rc)" "2"
check "the invalid calls left the registration unchanged" "$(jq -r .permission_mode "$REG")" "bypassPermissions"

out=$(in_pane work:0.0 "bash $RR register --permission-mode acceptEdits --remote-control off")
check "re-register with choices exits 0" "$(pane_rc)" "0"
check "permission mode recorded" "$(jq -r .permission_mode "$REG")" "acceptEdits"
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

finish
