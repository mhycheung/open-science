#!/bin/bash
# opsci: planted-leaks (fake SLURM job ids for the stub scheduler)
# The user's choices at registration reach the resumed session: permission mode,
# Remote Control, the Claude process's CLAUDE_CONFIG_DIR, and the launch command.
#
#   bash tests/slurm_resurrect/test_settings_carry.sh
#
# A registered session holding a fake Claude pane is rebuilt by rr_successor.sh.
# The launch command is a recorder that writes its argv and environment to a
# file, so the test sees exactly what a real `claude` would have been given.
# Control: a registration with the defaults must give bypassPermissions, Remote
# Control on and a session name; the non-default one must give neither.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
rr_test_setup
RR="$SCRIPTS/rr_registry.sh"
job 424242 RUNNING 1:00:00 2:00:00

cat > "$TMP/bin/recorder" <<REC
#!/bin/bash
out="$TMP/launch.\$\$"
{ echo "CCD=\${CLAUDE_CONFIG_DIR:-}"; echo "NAME=\${CLAUDE_CODE_SESSION_NAME:-}"
  printf 'ARG=%s\n' "\$@"; } > "\$out.tmp" && mv "\$out.tmp" "\$out"
exec sleep 600
REC
chmod +x "$TMP/bin/recorder"

# one_hop <state dir> <session> [register options...]: register, rebuild, and
# set $LAUNCH_OUT to the recorder output for the rebuilt Claude pane.
one_hop() {
  local st="$1" sess="$2"; shift 2
  export RR_STATE_DIR="$st"; mkdir -p "$st"; touch "$st/warning_shown"
  export SLURM_JOB_ID=424242
  start_tmux "$sess"
  fake_claude "$sess:0.0" "sid-$sess" "claude-test-model" >/dev/null
  tmux -S "$SOCK" new-window -d -t "$sess:1"
  in_pane "$sess:1" "bash $RR register $*" > "$TMP/reg.$sess.out"
  REG_RC=$(pane_rc)
  in_pane "$sess:1" "bash $RR set launch_cmd $TMP/bin/recorder" >/dev/null
  job 424242 COMPLETED            # the source job has ended: no handoff
  rm -f "$STUB/jobs/424242"
  rm -f "$TMP"/launch.*
  SLURM_JOB_ID=424243 RR_DELIVER_DRYRUN=1 RR_NO_COORDINATOR=1 RR_SUCCESSOR_NO_HOLD=1 \
    bash "$SCRIPTS/rr_successor.sh" 424242 > "$TMP/succ.$sess.out" 2>&1
  local i; for ((i=0; i<50; i++)); do ls "$TMP"/launch.* >/dev/null 2>&1 && break; sleep 0.2; done
  LAUNCH_OUT=$(cat "$TMP"/launch.* 2>/dev/null)
  job 424242 RUNNING 1:00:00 2:00:00
}

echo "== 1. non-default choices: acceptEdits, Remote Control off =="
one_hop "$TMP/rrA" alpha --permission-mode acceptEdits --remote-control off; out="$LAUNCH_OUT"
check "register accepted" "$REG_RC" "0"
has   "resumes the recorded session"            "$out" $'ARG=--resume\nARG=sid-alpha'
has   "same model"                              "$out" $'ARG=--model\nARG=claude-test-model'
has   "permission mode carried"                 "$out" $'ARG=--permission-mode\nARG=acceptEdits'
hasnt "no bypassPermissions"                    "$out" "bypassPermissions"
hasnt "Remote Control off -> no --remote-control" "$out" "ARG=--remote-control"
has   "Remote Control off -> no session name"   "$out" $'NAME=\n'
has   "CLAUDE_CONFIG_DIR restored from the process" "$out" "CCD=$TMP/cfg"
CARRIED="$TMP/rrA/registry/424243/alpha.json"
check "carried record keeps the permission mode" "$(jq -r .permission_mode "$CARRIED" 2>/dev/null)" "acceptEdits"
check "carried record keeps Remote Control off"  "$(jq -r .remote_control "$CARRIED" 2>/dev/null)" "false"
check "manifest records the permission mode" \
  "$(jq -r .permission_mode "$TMP/rrA/rr_manifest_424243.jsonl" 2>/dev/null)" "acceptEdits"

echo "== 2. control: the defaults (bypassPermissions, Remote Control on) =="
one_hop "$TMP/rrB" beta; out="$LAUNCH_OUT"
check "register accepted" "$REG_RC" "0"
has   "default permission mode is bypassPermissions" "$out" $'ARG=--permission-mode\nARG=bypassPermissions'
has   "Remote Control on -> --remote-control"   "$out" "ARG=--remote-control"
[[ "$out" =~ NAME=([^$'\n']+) ]] && ok "Remote Control on -> session name set (${BASH_REMATCH[1]})" \
  || bad "session name must be set" "$out"
check "carried record: Remote Control on" "$(jq -r .remote_control "$TMP/rrB/registry/424243/beta.json" 2>/dev/null)" "true"

echo "== 3. invalid choices are refused, nothing registered =="
export RR_STATE_DIR="$TMP/rrC"; mkdir -p "$RR_STATE_DIR"; touch "$RR_STATE_DIR/warning_shown"
out=$(in_pane beta:1 "bash $RR register --permission-mode yolo")
check "unknown permission mode -> exit 2" "$(pane_rc)" "2"
out=$(in_pane beta:1 "bash $RR register --remote-control maybe")
check "bad --remote-control value -> exit 2" "$(pane_rc)" "2"
[[ ! -e "$RR_STATE_DIR/registry/424242/beta.json" ]] && ok "nothing registered" || bad "nothing registered" "record exists"

finish
