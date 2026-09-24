#!/bin/bash
# rr_deliver.sh and the workspace trust dialog of a resumed session.
#
#   bash tests/slurm_resurrect/test_trust_dialog.sh
#
# Claude Code's trust dialog starts with the cursor on "No, exit", so a bare
# Enter quits the session. A fake dialog in a private tmux pane records which
# option it received. Cases:
#   1. dialog with the cursor on "No": rr_deliver must move to "Yes", then Enter.
#   2. control, no dialog on screen: rr_deliver must type nothing.
#   3. refusal, a dialog whose cursor cannot reach "Yes": rr_deliver must press
#      no Enter (which would choose "No, exit") and must notify the user.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
rr_test_setup

cat > "$TMP/bin/fakedialog" <<'FD'
#!/bin/bash
# fakedialog <out> <mode: dialog|stuck|none>
out="$1"; mode="$2"; sel=0
draw() {
  clear
  if [[ "$mode" == none ]]; then echo "> "; return; fi
  echo " Accessing workspace:"
  echo " Quick safety check: Is this a project you created or one you trust?"
  if [[ $sel == 0 ]]; then echo " ❯ No, exit"; echo "   Yes, I trust this folder"
  else echo "   No, exit"; echo " ❯ Yes, I trust this folder"; fi
  echo " Enter to confirm · Esc to cancel"
}
draw
while IFS= read -rsn1 k; do
  if [[ "$mode" == none ]]; then echo "input" >> "$out"; continue; fi
  if [[ "$k" == $'\e' ]]; then
    read -rsn2 -t 1 rest
    [[ "$rest" == "[B" && "$mode" == dialog ]] && sel=1
    [[ "$rest" == "[A" ]] && sel=0
    draw
  elif [[ -z "$k" ]]; then
    if [[ $sel == 1 ]]; then echo "yes" >> "$out"; mode=none; draw
    else echo "no" >> "$out"; exit 0; fi
  fi
done
FD
chmod +x "$TMP/bin/fakedialog"

start_tmux t
run_case() {  # <window> <mode>
  local w="$1" mode="$2"
  tmux -S "$SOCK" new-window -d -t "t:$w"
  tmux -S "$SOCK" send-keys -t "t:$w" "exec $TMP/bin/fakedialog $TMP/out.$w $mode" Enter
  sleep 1
  jq -nc --arg sock "$SOCK" --arg t "t:$w" \
    '{socket:$sock, target:$t, status:"idle", session_id:"sid-trust", note:"", note_src:""}' > "$TMP/m.$w.jsonl"
  RR_SETTLE_WAIT=0 RR_TRUST_KEY_WAIT=0.5 bash "$SCRIPTS/rr_deliver.sh" "$TMP/m.$w.jsonl" 2>&1
  sleep 1
}

echo "== 1. cursor on 'No': move to 'Yes', then Enter =="
out=$(run_case 1 dialog)
has "says it accepted the dialog" "$out" "accepted the trust dialog in t:1"
check "the dialog received 'yes' only" "$(cat "$TMP/out.1" 2>/dev/null)" "yes"

echo "== 2. control: no dialog on screen, nothing typed =="
out=$(run_case 2 none)
hasnt "no trust action logged" "$out" "trust dialog"
check "the pane received no input" "$(cat "$TMP/out.2" 2>/dev/null)" ""

echo "== 3. refusal: 'Yes' cannot be selected, no Enter pressed =="
out=$(run_case 3 stuck)
has "reports the failure" "$out" "FAILED to select 'Yes, I trust this folder' in t:3"
check "the dialog received no Enter (no 'no')" "$(cat "$TMP/out.3" 2>/dev/null)" ""
has "user notified" "$(cat "$RR_STATE_DIR/notifications.log" 2>/dev/null)" "stopped at the workspace trust dialog"

finish
