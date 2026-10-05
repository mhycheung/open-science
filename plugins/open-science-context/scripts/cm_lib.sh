# Shared helpers for the open-science context-management scripts. Source, do not run.
#
# On Claude Code the plugin's mod (hooks/context_mod.js) clears and resumes the session,
# wakes it, renames it and tracks token use inside Claude Code itself; see "the Claude Code
# mod" below. The tmux mechanics here are the fallback for a Claude Code that has not
# loaded the mod, and the mechanics for Codex.
#
# Everything that types into a Claude Code pane lives here, so there is one
# implementation of the tmux mechanics. The measured facts it relies on (Claude
# Code 2.1.220 to 2.1.280):
#   * The Stop hook fires about 1 s BEFORE the TUI leaves the busy state. Poll for
#     idle; never type at Stop time.
#   * `/clear` runs only when it is the FIRST thing in the input buffer. Send C-u first.
#   * A literal newline submits, so every typed line must be one line.
#   * Text and Enter are separate send-keys calls, with a pause between them.
#   * The TUI pads its empty prompt line with U+00A0; strip it before comparing.
#   * A message submitted while a turn runs is QUEUED ("Press up to edit queued
#     messages"). Queued counts as submitted.
#   * `<config>/sessions/<pid>.json` holds the live `sessionId` and `status`
#     (`busy`, `idle`, `shell`). The session id changes on /clear, which is how a
#     clear is confirmed.

OS_STATE="${OPSCI_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/open-science}"
OS_LOG="$OS_STATE/cm.log"
OS_PROMPT_RE='❯'
OS_BUSY_RE='esc to interrupt'

# ---- the state directory: private, owned by the user, written without following links ----
# The state directory holds records that the hooks and workers act on outside any sandbox
# (they type into panes, end and start Codex, run `codex queue`). It is therefore private:
# created 0700, files 0600 (umask 077 here; CM_UMASK0 keeps the caller's umask for what the
# scripts write elsewhere), and a directory owned by someone else is refused outright. Every
# write goes through a temporary file made by mktemp in the same directory and a rename,
# which replaces a symbolic link planted at the destination instead of writing through it.
#
# Under Codex the agent's tool shells run in a sandbox, and they must not be able to write
# what the hooks act on. They write only to the INBOX ($OS_STATE/inbox, the one directory
# the user makes writable for the sandbox); the Codex hook (cx_hook.sh, outside the
# sandbox) takes the inbox entries of its own pane and thread, checks them and writes the
# records itself. See "the Codex inbox" below.
CM_UMASK0=$(umask)
umask 077
OS_INBOX="$OS_STATE/inbox"
if [ -e "$OS_STATE" ] || [ -L "$OS_STATE" ]; then
  if [ ! -d "$OS_STATE" ] || [ ! -O "$OS_STATE" ]; then
    echo "open-science: the state directory $OS_STATE is not a directory owned by you; refusing to use it. Set OPSCI_STATE_DIR to a private directory of your own." >&2
    exit 1
  fi
  case "$(stat -c %a "$OS_STATE" 2>/dev/null)" in *00) ;; *) chmod go-rwx "$OS_STATE" 2>/dev/null ;; esac
fi
[ -L "$OS_LOG" ] && rm -f -- "$OS_LOG" 2>/dev/null
[ -f "$OS_LOG" ] && [ -O "$OS_LOG" ] && case "$(stat -c %a "$OS_LOG" 2>/dev/null)" in *00) ;; *) chmod go-rwx "$OS_LOG" 2>/dev/null ;; esac

# Make a directory (and its parents) 0700. Fails on a symbolic link or a directory that is
# not the user's.
cm_mkdir() {
  [ -L "$1" ] && return 1
  [ -d "$1" ] || mkdir -p -m 700 -- "$1" 2>/dev/null || return 1
  [ -O "$1" ] && [ ! -L "$1" ]
}

# A temporary file next to <dest> (mktemp: created new, never through a link), for an
# atomic write by rename.
cm_tmp_for() { cm_mkdir "$(dirname "$1")" && mktemp "$(dirname "$1")/.tmp.XXXXXX" 2>/dev/null; }

# Atomic write of stdin to <file>.
cm_write_text() {
  local f="$1" tmp
  tmp=$(cm_tmp_for "$f") || return 1
  cat > "$tmp" && mv -f -- "$tmp" "$f" || { rm -f -- "$tmp"; return 1; }
}

# Atomic write of jq's output: <file> then the jq arguments.
cm_jq_into() {
  local f="$1" tmp; shift
  tmp=$(cm_tmp_for "$f") || return 1
  jq "$@" > "$tmp" 2>/dev/null && mv -f -- "$tmp" "$f" || { rm -f -- "$tmp"; return 1; }
}

# A plain file this user owns: a regular file, not a symbolic link, one link, at most 64 KB.
cm_plain_file() {
  [ -f "$1" ] && [ ! -L "$1" ] && [ -O "$1" ] || return 1
  [ "$(stat -c '%h %s' "$1" 2>/dev/null | awk '$1==1 && $2<=65536 {print "ok"}')" = ok ]
}

# A context file named in a record: an absolute path with no whitespace or control
# character, to an existing regular file. The resume prompt is built from it, never read
# from a record.
cm_ctx_ok() {
  local c="${1:-}"
  case "$c" in /*) ;; *) return 1 ;; esac
  case "$c" in *[[:space:]]*) return 1 ;; esac
  [ "$(cm_one_line "$c")" = "$c" ] || return 1
  [ -f "$c" ]
}
cm_claude_prompt() { printf '/open-science-context:continue-context %s' "$1"; }   # <context file>

# A tmux socket this user owns, and a pane id (%N): checked before anything is typed.
cm_sock_ok() { [ -S "${1:-}" ] && [ -O "$1" ] && [[ "${2:-}" =~ ^%[0-9]+$ ]]; }

cm_log() {
  cm_mkdir "$OS_STATE" || return 0
  [ -L "$OS_LOG" ] && rm -f -- "$OS_LOG" 2>/dev/null
  { echo "[$(date -Iseconds)] $*" >> "$OS_LOG"; } 2>/dev/null
  return 0
}

# Which jumps the user allows: all (default), wait (wait and cache-cold jumps only) or off.
# Set as OPSCI_JUMPS in the "env" block of the Claude settings.json (onboarding asks).
# An unknown value counts as all, so a typo does not silently switch jumps off.
cm_jump_mode() {
  case "${OPSCI_JUMPS:-all}" in off|wait) echo "$OPSCI_JUMPS" ;; *) echo all ;; esac
}

cm_key() { printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'; }

# ---- the Claude Code mod ----------------------------------------------------------
# The mod sets OPSCI_MOD=1 when it loads, for Claude Code and every process it starts
# after (hooks, tool shells). With it set, the plain Stop and naming hooks do nothing and
# jump.sh, wait_slurm.sh --notify and the mod (through cm_stop.sh --mod and cm_mod.sh) key
# their records by session id instead of by tmux pane, so tmux is not needed. A session
# id survives `claude --resume`, so these records also survive a SLURM resurrection.
cm_mod_active() { [ "${OPSCI_MOD:-}" = 1 ] && [ "$(cm_runtime)" = claude ]; }
cm_sess_key() { printf 'claude-sid__%s' "$(cm_key "$1")"; }   # <session id>

# The texts the Stop policy sends, the same for the mod and the tmux path.
cm_cold_notice() {  # <seconds idle>
  local idle
  if [ "$1" -ge 60 ]; then idle="$(( $1 / 60 )) min"; else idle="$1 s"; fi
  printf '[open-science] cache-cold: %s idle with work still running. Do a wait jump now (open-science-context:context-management).' "$idle"
}
cm_cold_seconds() { local m="${OPSCI_CACHE_COLD_MIN:-58}"; printf '%s' "${OPSCI_CACHE_COLD_SECONDS:-$(( m * 60 ))}"; }

# Key for this pane: tmux socket + pane id. Pane ids are unique only per server.
cm_pane_key() {  # [sock] [pane] -> key, or return 1 outside tmux
  local t="${TMUX:-}"; local sock="${1:-${t%%,*}}" pane="${2:-${TMUX_PANE:-}}"
  [ -n "${TMUX:-}${1:-}" ] && [ -n "$pane" ] || return 1
  printf '%s__%s' "$(cm_key "$sock")" "$(cm_key "$pane")"
}

cm_request_path() { printf '%s/jump/%s.json' "$OS_STATE" "$1"; }   # <pane key>
cm_reg_path()     { printf '%s/pane_context/%s.json' "$OS_STATE" "$1"; }   # <pane key>; pane_context.sh's record
cm_timer_path()   { printf '%s/timer/%s.pid' "$OS_STATE" "$1"; }   # <pane key>
# Open it with `exec 9<>` (no truncation); a symbolic link there is removed first.
cm_lock_path()    {
  local f="$OS_STATE/lock/$1.lock"
  cm_mkdir "$OS_STATE/lock"; [ -L "$f" ] && rm -f -- "$f"
  printf '%s' "$f"
}

# The claude process that owns this shell: walk up the process tree.
cm_claude_pid() {
  local p="${1:-$$}" c
  while [ -n "$p" ] && [ "$p" -gt 1 ] 2>/dev/null; do
    c=$(ps -o comm= -p "$p" 2>/dev/null)
    [ "$c" = claude ] && { echo "$p"; return 0; }
    p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
  done
  return 1
}

# That process's state file, or return 1.
cm_state_file() {  # <claude pid>
  local d f
  for d in "${CLAUDE_CONFIG_DIR:-}" "$HOME/.claude"; do
    [ -n "$d" ] || continue
    f="$d/sessions/$1.json"
    [ -f "$f" ] && { echo "$f"; return 0; }
  done
  return 1
}

cm_sid()    { jq -r '.sessionId // empty' "$1" 2>/dev/null; }   # <state file>
cm_status() { jq -r '.status // empty' "$1" 2>/dev/null; }      # <state file>

# The live session id of the agent above this shell. Claude: its state file, which
# follows /clear, else $CLAUDE_CODE_SESSION_ID. Codex: $CODEX_THREAD_ID, which Codex
# sets in every tool shell (a Codex jump starts a new process, so it never goes stale).
# Prints nothing when none is known.
cm_live_sid() {
  local cpid sf sid=""
  if [ "$(cm_runtime)" = codex ]; then printf '%s' "${CODEX_THREAD_ID:-}"; return 0; fi
  cpid=$(cm_claude_pid) && sf=$(cm_state_file "$cpid") && sid=$(cm_sid "$sf")
  printf '%s' "${sid:-${CLAUDE_CODE_SESSION_ID:-}}"
}

# The session that registered this pane (pane_context.sh set), or nothing. Only that
# session gets the Stop hook's timer and size notice: registration is the opt-in.
cm_registered_sid() {  # <pane key>
  jq -r '.session_id // empty' "$(cm_reg_path "$1")" 2>/dev/null
}

# Hand the pane's registration to the session a jump created. No record, no change.
cm_reg_handover() {  # <pane key> <new sid>
  local f; f=$(cm_reg_path "$1")
  [ -f "$f" ] || return 0
  cm_jq_into "$f" --arg sid "$2" '.session_id=$sid' "$f"
}

# ---- pane reading ------------------------------------------------------------
cm_strip() { sed -e 's/\xc2\xa0//g' -e 's/\xe2\x80\x8b//g' -e 's/\xef\xbb\xbf//g' | tr -d '[:space:]'; }
cm_cap()   { tmux -S "$1" capture-pane -p -t "$2" 2>/dev/null; }          # <sock> <pane>
cm_keys()  { local s="$1" t="$2"; shift 2; tmux -S "$s" send-keys -t "$t" "$@" 2>/dev/null; }

cm_pane_queued() { cm_cap "$1" "$2" | grep -qiE 'queued message|press up to edit queued'; }

cm_input_empty() {  # <sock> <pane>: the live prompt line holds no text
  local last
  last=$(cm_cap "$1" "$2" | grep "$OS_PROMPT_RE" | tail -1)
  [ -n "$last" ] || return 1
  last="${last#*❯}"
  printf '%s' "$last" | grep -qiE 'queued message|press up to edit queued' && return 0
  [ -z "$(printf '%s' "$last" | cm_strip)" ]
}

# Visible text of the input box, from the last prompt glyph to the box border.
cm_input_box() {  # <sock> <pane>
  local cap ln
  cap=$(cm_cap "$1" "$2") || return 1
  ln=$(printf '%s\n' "$cap" | grep -n "$OS_PROMPT_RE" | tail -1 | cut -d: -f1)
  [ -n "$ln" ] || return 1
  printf '%s\n' "$cap" | tail -n +"$ln" | sed '1s/.*❯//' | awk 'index($0,"─"){exit} {print}' | cm_strip
}

cm_clear_input() {  # <sock> <pane>; C-u clears one visual line, BSpace bursts the rest
  local i
  for ((i=0; i<30; i++)); do
    cm_input_empty "$1" "$2" && return 0
    if (( i % 10 == 9 )); then cm_keys "$1" "$2" -N 200 BSpace; sleep 1
    else cm_keys "$1" "$2" C-u; sleep 0.3; fi
  done
  cm_input_empty "$1" "$2"
}

# Idle = the pane shows the prompt, no busy marker, and the state file (when
# readable) does not say busy. A background subagent pins status at "busy" after
# the turn has ended, so a pane that has looked typable for $OPSCI_BG_QUIET polls in a
# row outvotes the status. "shell" with the prompt visible is idle (a background
# shell outlived the turn).
CM_QUIET=0
cm_idle_once() {  # <sock> <pane> <state file or empty>
  local snap st
  snap=$(cm_cap "$1" "$2")
  if printf '%s' "$snap" | grep -q "$OS_BUSY_RE"; then CM_QUIET=0; return 1; fi
  if printf '%s' "$snap" | grep -q "$OS_PROMPT_RE"; then CM_QUIET=$((CM_QUIET+1)); else CM_QUIET=0; return 1; fi
  [ -n "${3:-}" ] || return 0
  st=$(cm_status "$3")
  case "$st" in
    ""|idle|shell) return 0 ;;
  esac
  [ "$CM_QUIET" -ge "${OPSCI_BG_QUIET:-20}" ] && return 0
  return 1
}

# Wait until the pane has been idle for $OPSCI_IDLE_STABLE consecutive 1-s polls.
cm_wait_idle() {  # <sock> <pane> <state file or empty> <timeout s>
  local waited=0 stable=0
  CM_QUIET=0
  while [ "$waited" -lt "$4" ]; do
    if cm_idle_once "$1" "$2" "$3"; then stable=$((stable+1)); else stable=0; fi
    [ "$stable" -ge "${OPSCI_IDLE_STABLE:-5}" ] && return 0
    sleep 1; waited=$((waited+1))
  done
  return 1
}

# One line of text to type: newlines and tabs become spaces, and every other control
# character (C0 including ESC and CR, DEL, and C1 in UTF-8) is removed, so typed text can
# never be read by the terminal or the TUI as a key or an escape sequence.
cm_one_line() {
  printf '%s' "$1" | tr '\n\t' '  ' | LC_ALL=C tr -d '\000-\037\177' | LC_ALL=C sed 's/\xc2[\x80-\x9f]//g'
}

# Type one line and confirm it was submitted. 0 = submitted, 1 = not.
# Uses the paste buffer (one write) and checks the END of the payload, which the
# input box always keeps on screen even when a long line scrolls its head away.
cm_deliver() {  # <sock> <pane> <text> [attempts]
  local sock="$1" pane="$2" text attempts="${4:-3}" i w box want payload buf="osdeliver$$"
  cm_sock_ok "$sock" "$pane" || { cm_log "deliver: $sock $pane is not a tmux socket of this user and a pane id; nothing typed"; return 1; }
  text=$(cm_one_line "$3")
  [ -n "$text" ] || return 1
  want=$(printf '%s' "$text" | rev | cut -c1-24 | rev | cm_strip)
  payload=$(printf '%s' "$text" | cm_strip)
  for ((i=1; i<=attempts; i++)); do
    cm_input_empty "$sock" "$pane" || cm_clear_input "$sock" "$pane" || true
    printf '%s' "$text" | tmux -S "$sock" load-buffer -b "$buf" - 2>/dev/null || continue
    tmux -S "$sock" paste-buffer -d -b "$buf" -t "$pane" 2>/dev/null || continue
    sleep 3
    box=$(cm_input_box "$sock" "$pane")
    if [ -z "$box" ] || [[ "$box" != *"$want"* ]] || [[ "$payload" != *"$box"* ]]; then
      cm_log "deliver: $pane input box does not hold the payload (attempt $i/$attempts)"
      cm_clear_input "$sock" "$pane" || true
      continue
    fi
    cm_keys "$sock" "$pane" Enter
    for ((w=0; w<20; w++)); do
      sleep 1
      if cm_cap "$sock" "$pane" | grep -q "$OS_BUSY_RE" || cm_pane_queued "$sock" "$pane" \
         || cm_input_empty "$sock" "$pane"; then
        cm_log "deliver: submitted to $pane (attempt $i)"
        return 0
      fi
    done
    cm_log "deliver: Enter did not drain $pane's buffer (attempt $i/$attempts)"
  done
  cm_input_empty "$sock" "$pane" || cm_clear_input "$sock" "$pane" || true
  cm_log "deliver: FAILED on $pane after $attempts attempts; nothing submitted"
  return 1
}

# Kill this pane's cache-cold timer, if any.
cm_timer_kill() {  # <pane key>
  local f pid
  f=$(cm_timer_path "$1")
  [ -f "$f" ] || return 0
  pid=$(cat "$f" 2>/dev/null)
  [ -n "$pid" ] && kill -- "-$pid" 2>/dev/null || { [ -n "$pid" ] && kill "$pid" 2>/dev/null; }
  rm -f "$f"
}

# ---- runtime adapter (Claude Code or Codex) ------------------------------------
# Which agent runs this shell: claude, codex, or none. $OPSCI_RUNTIME overrides.
# Claude Code sets CLAUDECODE=1 in its tool shells; Codex sets CODEX_THREAD_ID. Codex
# runs tool shells in a sandbox with its own pid namespace (measured, codex-cli
# 0.159.3), so the process tree cannot find it there; its hooks are children of the
# TUI and have no CODEX_THREAD_ID, so there the tree walk decides. With both
# variables set (one agent started inside the other) the nearer ancestor wins, and a
# walk that finds neither means the Codex sandbox.
cm_runtime() {
  case "${OPSCI_RUNTIME:-}" in claude|codex) echo "$OPSCI_RUNTIME"; return 0 ;; esac
  local near; near=$(cm_agent_ancestor)
  if [ -n "${CODEX_THREAD_ID:-}" ] && [ -n "${CLAUDECODE:-}" ]; then echo "${near:-codex}"; return 0; fi
  [ -n "${CLAUDECODE:-}" ] && { echo claude; return 0; }
  [ -n "${CODEX_THREAD_ID:-}" ] && { echo codex; return 0; }
  echo "${near:-none}"
}

# Name (claude|codex) of the nearest agent process above this shell, or nothing.
cm_agent_ancestor() {
  local p="${1:-$$}" c n=0
  while [ -n "$p" ] && [ "$p" -gt 1 ] 2>/dev/null && [ "$n" -lt 64 ]; do
    c=$(ps -o comm= -p "$p" 2>/dev/null)
    case "$c" in claude|codex) echo "$c"; return 0 ;; esac
    p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' '); n=$((n+1))
  done
  return 1
}

# The codex process above this shell (a hook's parent chain), or return 1.
cm_codex_pid() {
  local p="${1:-$$}" n=0
  while [ -n "$p" ] && [ "$p" -gt 1 ] 2>/dev/null && [ "$n" -lt 64 ]; do
    [ "$(ps -o comm= -p "$p" 2>/dev/null)" = codex ] && { echo "$p"; return 0; }
    p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' '); n=$((n+1))
  done
  return 1
}

# ---- Codex session records ------------------------------------------------------
# Codex has no state file like Claude's sessions/<pid>.json, and nothing outside the
# TUI can tell which thread runs in which pane. The Codex hooks (cx_hook.sh) write:
#   codex/threads/<thread id>.json   status (busy|idle), TUI pid, pane, model, rollout
#   codex/panes/<pane key>.json      the same record for the thread last seen in a pane
# Both are written only by hooks, which Codex runs outside its sandbox.
cm_cx_thread_path() { printf '%s/codex/threads/%s.json' "$OS_STATE" "$(cm_key "$1")"; }
cm_cx_pane_path()   { printf '%s/codex/panes/%s.json' "$OS_STATE" "$1"; }   # <pane key>

# Session-keyed context registration (pane_context.sh), for work outside tmux.
cm_sess_reg_path() { printf '%s/session_context/%s__%s.json' "$OS_STATE" "$1" "$(cm_key "$2")"; }  # <runtime> <sid>

# Atomic JSON write: <file> then the jq arguments that build it with -n.
cm_write_json() { local f="$1"; shift; cm_jq_into "$f" -n "$@"; }

# The resume prompt for a Codex session. Codex has no /open-science-context:...
# slash command; the prompt names the skill in words. $OPSCI_CODEX_RESUME_PROMPT
# overrides it, with {ctx} standing for the context file.
cm_codex_prompt() {  # <context file>
  local t="${OPSCI_CODEX_RESUME_PROMPT:-Use the open-science-context continue-context skill to continue from {ctx}}"
  printf '%s' "${t//\{ctx\}/$1}"
}

# Is <pid> a live codex process of this user?
cm_is_codex() {
  [ -n "${1:-}" ] && [ "$(ps -o comm= -p "$1" 2>/dev/null)" = codex ] \
    && [ "$(ps -o uid= -p "$1" 2>/dev/null | tr -d ' ')" = "$(id -u)" ]
}

# The CODEX_HOME to run `codex queue` with for thread <tid>: the one its hook recorded, but
# only when it is this process's own $CODEX_HOME or ~/.codex (a forged record must not make
# codex load another config). Prints nothing for the default.
cm_cx_home() {  # <thread id>
  local h
  h=$(jq -r '.codex_home // ""' "$(cm_cx_thread_path "$1")" 2>/dev/null)
  [ -n "$h" ] || return 0
  if [ "$h" = "${CODEX_HOME:-}" ] || [ "$h" = "$HOME/.codex" ]; then printf '%s' "$h"; return 0; fi
  cm_log "codex_home '$h' in the record of thread ${1:0:8} is neither \$CODEX_HOME nor ~/.codex; ignored"
}

# Is <pid> a descendant of (or equal to) <ancestor>?
cm_descends() {  # <pid> <ancestor>
  local p="$1" n=0
  while [ -n "$p" ] && [ "$p" -gt 1 ] 2>/dev/null && [ "$n" -lt 64 ]; do
    [ "$p" = "$2" ] && return 0
    p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' '); n=$((n+1))
  done
  return 1
}

# The command line that starts a fresh Codex TUI like the one running as <pid>, with
# <prompt> as its first message, printed as one shell-quoted line. It keeps the
# process's own options (model, profile, sandbox, approval policy, config overrides,
# added dirs: exactly what the user chose, nothing added) and drops what belongs to
# the old thread: a positional prompt, images, `resume`/`fork` and their arguments.
# <model>, when given, replaces --model (the hooks report the model in use, which
# /model may have changed). Returns 1 with a reason on stderr for anything it does
# not understand: an unknown option is refused, not guessed at.
cm_codex_relaunch_cmd() {  # <pid> <prompt> [model]
  local pid="$1" prompt="$2" model="${3:-}" a skip=0 i first=1 out=() home
  local -a argv=()
  [ -r "/proc/$pid/cmdline" ] || { echo "cannot read /proc/$pid/cmdline" >&2; return 1; }
  mapfile -d '' -t argv < "/proc/$pid/cmdline"
  i=1   # an interpreter running a codex script (node codex.js, bash codex): skip the script
  case "$(basename -- "${argv[1]:-x}")" in codex|codex.js) [ -f "${argv[1]}" ] && i=2 ;; esac
  for (( ; i<${#argv[@]}; i++)); do
    a="${argv[$i]}"
    case "$skip" in
      keep) out+=("$a"); skip=0; continue ;;
      drop) skip=0; continue ;;
    esac
    case "$a" in
      -c|--config|--enable|--disable|--remote|--remote-auth-token-env|--local-provider|-p|--profile|-s|--sandbox|-C|--cd|--add-dir|-a|--ask-for-approval)
        out+=("$a"); skip=keep ;;
      --config=*|--enable=*|--disable=*|--remote=*|--remote-auth-token-env=*|--local-provider=*|--profile=*|--sandbox=*|--cd=*|--add-dir=*|--ask-for-approval=*)
        out+=("$a") ;;
      --oss|--approve-for-me|--dangerously-bypass-approvals-and-sandbox|--dangerously-bypass-hook-trust|--search|--no-alt-screen|--no-daemon|--strict-config)
        out+=("$a") ;;
      -m|--model) [ -n "$model" ] || model="${argv[$((i+1))]:-}"; skip=drop ;;
      --model=*) [ -n "$model" ] || model="${a#--model=}" ;;
      -i|--image) skip=drop ;;
      --image=*|--last|--all|--include-non-interactive) ;;
      --worktree) echo "codex runs with --worktree; a relaunch would make another worktree" >&2; return 1 ;;
      --) break ;;
      -*) echo "unknown codex option '$a'; not relaunching" >&2; return 1 ;;
      *)  # positional: the first may be a subcommand; the rest are a prompt or a thread id
        if [ "$first" = 1 ]; then
          first=0
          case "$a" in
            resume|fork) ;;
            exec|e|review|login|logout|mcp|plugin|app-server|remote-control|completion|update|doctor|sandbox|debug|apply|queue|archive|delete|migrate-rollouts|unarchive|cloud|exec-server|features|agents|help)
              echo "codex process runs '$a', not the interactive TUI" >&2; return 1 ;;
          esac
        fi ;;
    esac
  done
  [ -n "$model" ] && out=(-m "$model" "${out[@]}")
  home=$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | sed -n 's/^CODEX_HOME=//p' | head -1)
  local line="" x
  [ -n "$home" ] && line="CODEX_HOME=$(printf '%q' "$home") "
  line="${line}codex"
  for x in "${out[@]}"; do line="$line $(printf '%q' "$x")"; done
  [ -n "$prompt" ] && line="$line $(printf '%q' "$prompt")"
  printf '%s\n' "$line"
}

# Start time of a process (field 22 of /proc/<pid>/stat, in clock ticks since boot), or
# nothing. A pid with a different start time is a different process: records keep both,
# so a reused pid is never mistaken for the process they describe.
cm_proc_start() { sed 's/.*) //' "/proc/$1/stat" 2>/dev/null | awk '{print $20}'; }

# Is <pid> still the codex process a record describes? <start>, when recorded, must match.
cm_is_codex_proc() {  # <pid> [start]
  cm_is_codex "${1:-}" || return 1
  [ -z "${2:-}" ] || [ "$(cm_proc_start "$1")" = "$2" ]
}

# ---- the state directory under the Codex sandbox: the inbox ------------------------------
# Codex's workspace-write and read-only sandboxes make everything outside the workspace,
# /tmp and the added directories read-only (measured with `codex sandbox`, codex-cli
# 0.159.3), so a Codex tool shell cannot write the default $OS_STATE under ~/.local/state.
# It must not: the hooks and workers act on those records outside the sandbox. The user
# makes ONE subdirectory writable for it, the inbox (verified live: `--add-dir` makes a
# directory writable):
#   mkdir -p <state dir>/inbox && codex --add-dir <state dir>/inbox ...
# or, for every start, `sandbox_workspace_write.writable_roots = ["<state dir>/inbox"]` in
# ~/.codex/config.toml. Nothing here widens a sandbox by itself.
#
# A Codex tool shell writes, under inbox/<target>/ (<target>: the pane key in tmux, else
# thread__<thread id>):
#   context.json        {thread_id, doc_path}   pane_context.sh set ("" for clear)
#   jump.json           {thread_id, kind, context}   jump.sh active|wait
#   wakers/<name>.json  {thread_id, jobs}       wait_slurm.sh --notify
# The Codex hook takes the whole inbox/<target> directory of its own pane and thread (a
# rename into a private staging directory, so nothing in it can change or be swapped for a
# link afterwards), accepts only plain files of its own thread with valid values, writes
# the records itself, and deletes the rest. Nothing a tool shell writes is ever executed,
# typed or used as a path to write to.
cm_inbox_target() {  # [thread id] -> this shell's inbox target
  local k; k=$(cm_pane_key) && { printf '%s' "$k"; return 0; }
  printf 'thread__%s' "$(cm_key "${1:-${CODEX_THREAD_ID:-}}")"
}
cm_inbox_dir() { printf '%s/%s' "$OS_INBOX" "$1"; }   # <target>

# For the hook: move inbox/<target> into a new private staging directory and print the
# path it has there, or return 1 when there is nothing (or not a real directory) to take.
cm_inbox_take() {  # <target>
  local src="$OS_INBOX/$1" stage
  [ -e "$src" ] || [ -L "$src" ] || return 1
  cm_mkdir "$OS_STATE/staging" || return 1
  stage=$(mktemp -d "$OS_STATE/staging/XXXXXX" 2>/dev/null) || return 1
  if ! mv -f -- "$src" "$stage/in" 2>/dev/null; then rmdir "$stage"; return 1; fi
  if [ -L "$stage/in" ] || [ ! -d "$stage/in" ] || [ ! -O "$stage/in" ]; then
    cm_log "inbox: $1 is not a directory of this user; removed"
    rm -rf -- "$stage"; return 1
  fi
  printf '%s/in' "$stage"
}

# Can this shell write what it must? Under Codex: the inbox; otherwise the state directory.
cm_state_writable() {
  local d t
  if [ "$(cm_runtime)" = codex ]; then d="$OS_INBOX"; else d="$OS_STATE"; fi
  mkdir -p "$d" 2>/dev/null || return 1
  t="$d/.write_test.$$"
  ( : > "$t" ) 2>/dev/null || return 1
  rm -f "$t"
}
# Under Codex: can this shell write the hooks' part of the state directory? It should not.
cm_state_too_open() {
  local t="$OS_STATE/.write_test.$$"
  [ "$(cm_runtime)" = codex ] || return 1
  ( : > "$t" ) 2>/dev/null || return 1
  rm -f "$t"
}
cm_state_hint() {
  printf 'open-science: the open-science inbox %s is not writable here (under Codex: the sandbox). Context registration, jumps and wakers need it. Start Codex with it as an extra writable directory:\n  mkdir -p %q && codex --add-dir %q\nor add it to sandbox_workspace_write.writable_roots in ~/.codex/config.toml. Make only the inbox writable, not the whole state directory %s. A context file named explicitly still works without it (continue-context <file>).\n' \
    "$OS_INBOX" "$OS_INBOX" "$OS_INBOX" "$OS_STATE"
}
cm_state_open_hint() {
  printf 'open-science: WARNING: this Codex session can write the whole state directory %s, so the sandbox can forge the records that the hooks act on outside it. Make only the inbox writable: replace %s with %s in --add-dir or in sandbox_workspace_write.writable_roots (~/.codex/config.toml). (Without a sandbox this warning does not apply.)\n' \
    "$OS_STATE" "$OS_STATE" "$OS_INBOX"
}

# Update a JSON record under its directory's lock: <file> <jq filter> [jq args...].
# The lock is flock on the directory itself (opened read-only: nothing is created or
# truncated). Returns 1 (and leaves the file alone) if the file is gone or jq fails.
cm_json_update() {
  local f="$1" flt="$2"; shift 2
  (
    exec 8<"$(dirname "$f")" && flock -w 30 8 || exit 1
    cm_plain_file "$f" || exit 1
    cm_jq_into "$f" "$@" "$flt" "$f" || exit 1
  )
}
