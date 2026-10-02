#!/usr/bin/env bash
# The shell side of the Claude Code mod (hooks/context_mod.js). The mod does what needs
# Claude Code itself (clear, run a prompt or command, rename, timers, token counts); the
# records stay in shell, in the same state directory and formats as the tmux path, so the
# two paths share one set of files. The mod calls:
#
#   cm_mod.sh handover <old sid> <new sid>   a clear made a new session: copy the old
#                                            session's registration to it, point the pane
#                                            record (if any) at it, and move its queued wakers
#   cm_mod.sh waiting <sid> <context file>   a wait jump left session <sid> waiting
#   cm_mod.sh woken <sid>                    the session took a turn; it is no longer waiting
#   cm_mod.sh resumed <sid>                  at load: if <sid> was left waiting by ANOTHER
#                                            claude process and has no queued waker, print the
#                                            prompt that wakes it (its background tasks did not
#                                            survive the restart, e.g. a SLURM resurrection)
#   cm_mod.sh wakers <sid>                   list the session's queued waker files
#   cm_mod.sh log <text>                     a line in the state directory's cm.log
#
# A session id survives `claude --resume`, so every record here survives a resurrection.
# Never fails loudly: the mod treats any error as "nothing to do".
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cm_lib.sh
. "$HERE/cm_lib.sh"
command -v jq >/dev/null 2>&1 || exit 0

waiting_path() { printf '%s/mod_waiting/%s.json' "$OS_STATE" "$(cm_sess_key "$1")"; }
waker_dir()    { printf '%s/wakers/%s' "$OS_STATE" "$(cm_sess_key "$1")"; }

case "${1:-}" in
  handover)
    old="${2:-}"; new="${3:-}"
    [ -n "$old" ] && [ -n "$new" ] && [ "$old" != "$new" ] || exit 0
    src=$(cm_sess_reg_path claude "$old")
    if [ -f "$src" ]; then
      dst=$(cm_sess_reg_path claude "$new")
      jq --arg sid "$new" --arg at "$(date -Iseconds)" '.session_id=$sid | .handed_over_at=$at' "$src" > "$dst.tmp.$$" \
        && mv "$dst.tmp.$$" "$dst" || rm -f "$dst.tmp.$$"
      # The pane record, when this session runs in tmux, follows too: the tmux path and
      # an optional resurrection component read it.
      if KEY=$(cm_pane_key) && [ "$(cm_registered_sid "$KEY")" = "$old" ]; then cm_reg_handover "$KEY" "$new"; fi
    fi
    if [ -d "$(waker_dir "$old")" ]; then
      mkdir -p "$(waker_dir "$new")"
      for f in "$(waker_dir "$old")"/*.json; do
        [ -f "$f" ] && mv "$f" "$(waker_dir "$new")/"
      done
      rmdir "$(waker_dir "$old")" 2>/dev/null
    fi
    cm_log "mod: session ${old:0:8} -> ${new:0:8}; registration and wakers handed over" ;;
  waiting)
    sid="${2:-}"; ctx="${3:-}"; [ -n "$sid" ] || exit 0
    cpid=$(cm_claude_pid) || cpid=""
    cm_write_json "$(waiting_path "$sid")" --arg sid "$sid" --arg ctx "$ctx" --arg pid "$cpid" \
      --arg start "$([ -n "$cpid" ] && cm_proc_start "$cpid")" --arg at "$(date -Iseconds)" \
      '{version:1, session_id:$sid, context:$ctx, claude_pid:$pid, claude_start:$start, since:$at}' ;;
  woken)
    [ -n "${2:-}" ] && rm -f "$(waiting_path "$2")" ;;
  resumed)
    sid="${2:-}"; f=$(waiting_path "$sid"); [ -n "$sid" ] && [ -f "$f" ] || exit 0
    cpid=$(cm_claude_pid) || cpid=""
    # The same process (the mod was reloaded): its background tasks still run.
    if [ -n "$cpid" ] && [ "$(jq -r .claude_pid "$f")" = "$cpid" ] \
       && [ "$(jq -r .claude_start "$f")" = "$(cm_proc_start "$cpid")" ]; then exit 0; fi
    # A queued waker survives the restart; the mod polls it again.
    ls "$(waker_dir "$sid")"/*.json >/dev/null 2>&1 && exit 0
    ctx=$(jq -r '.context // empty' "$f"); rm -f "$f"
    cm_log "mod: session ${sid:0:8} was waiting in another process; waking it from ${ctx:-its registration}"
    printf '/open-science-context:continue-context %s\n' "$ctx" ;;
  wakers)
    [ -n "${2:-}" ] && ls "$(waker_dir "$2")"/*.json 2>/dev/null ;;
  log)
    shift; cm_log "mod: $*" ;;
  *) echo "usage: cm_mod.sh {handover <old> <new>|waiting <sid> <ctx>|woken <sid>|resumed <sid>|wakers <sid>|log <text>}" >&2; exit 2 ;;
esac
exit 0
