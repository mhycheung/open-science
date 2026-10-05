#!/usr/bin/env bash
# Hook of the open-science plugin (Claude Code and Codex): tell the user, once per session,
# that a newer open-science release exists. The agent is never told and never asked.
#
#   SessionStart   if the last check is older than a day, look up the framework's newest
#                  release in the background (the version in the open-science plugin's
#                  manifest on the repository's default branch). Prints nothing.
#   Stop           if the newest release known is newer than the version this session
#                  loaded, and this session has not shown the notice yet, show it to the
#                  user (a `systemMessage`, which both agents show to the user and not to
#                  the model). Later turns of the same session show nothing. The newest
#                  release known is the higher of the last lookup's and the version in the
#                  local marketplace checkout's manifest (which `claude plugin update`
#                  brings up to date). If that release is already installed on disk, the
#                  notice says a new session loads it instead of giving update commands.
#
# The repository is $OPSCI_UPDATE_REPO, else the git remote of the marketplace checkout the
# plugin was installed from, else the framework's GitHub repository. OPSCI_UPDATE_CHECK=off
# turns the check off. A failed lookup (no network, no access) shows nothing. The hook never
# fails.
set -uo pipefail

DEFAULT_REPO="https://github.com/mhycheung/open-science.git"
MANIFEST="plugins/open-science/.claude-plugin/plugin.json"
INTERVAL="${OPSCI_UPDATE_INTERVAL:-86400}"  # seconds between lookups

[ "${OPSCI_UPDATE_CHECK:-on}" = off ] && { cat >/dev/null 2>&1; exit 0; }
ROOT="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}}"
DATA="${PLUGIN_DATA:-${CLAUDE_PLUGIN_DATA:-${XDG_CACHE_HOME:-$HOME/.cache}/opsci/update-check}}"
mkdir -p "$DATA/told" 2>/dev/null || exit 0

version_of() {  # the "version" of a plugin.json on stdin; only [0-9A-Za-z.+-] is kept, so a
                # remote manifest cannot put other text (or a quote that breaks the JSON) in the notice
  sed -n 's/^[[:space:]]*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1 | clean_version
}

clean_version() { tr -cd '0-9A-Za-z.+-' | cut -c 1-40; }

newest() { printf '%s\n' "$@" | grep . | sort -V | tail -n 1; }  # the highest version given

# an installed plugin is <base>/cache/<marketplace>/<plugin>/<version>; its marketplace
# checkout is <base>/marketplaces/<marketplace>
MKT=$(basename "$(dirname "$(dirname "$ROOT")")")
BASE=$(dirname "$(dirname "$(dirname "$(dirname "$ROOT")")")")

repo_url() {
  if [ -n "${OPSCI_UPDATE_REPO:-}" ]; then echo "$OPSCI_UPDATE_REPO"; return; fi
  local url
  url=$(git -C "$BASE/marketplaces/$MKT" remote get-url origin 2>/dev/null) && [ -n "$url" ] \
    && { echo "$url"; return; }
  echo "$DEFAULT_REPO"
}

plugin_version() {  # the version in the plugin.json of the plugin directory $1
  local v
  v=$(version_of < "$1/.claude-plugin/plugin.json" 2>/dev/null)
  [ -n "$v" ] || v=$(version_of < "$1/.codex-plugin/plugin.json" 2>/dev/null)
  echo "$v"
}

on_disk() {  # the newest version of this plugin installed on disk: Claude Code's record
             # (installed_plugins.json), else the version directories next to $ROOT
  local v d
  v=$(awk -v key="\"open-science@$MKT\"" '
        index($0, key) { on = 1 }
        on && /"version"/ { sub(/.*"version"[[:space:]]*:[[:space:]]*"/, ""); sub(/".*/, ""); gsub(/[^0-9A-Za-z.+-]/, ""); print }
        on && /\]/ { exit }' "$BASE/installed_plugins.json" 2>/dev/null)
  if [ -z "$v" ]; then
    for d in "$(dirname "$ROOT")"/*/; do v="$v $(plugin_version "$d")"; done
  fi
  newest $v
}

refresh() {  # write the newest release's version to $DATA/latest
  local tmp v
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/opsci-update.XXXXXX") || return
  export GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=10"
  if timeout 60 git clone --quiet --depth 1 --filter=blob:none --no-checkout "$(repo_url)" "$tmp/r" \
       >/dev/null 2>&1; then
    v=$(timeout 30 git -C "$tmp/r" show "HEAD:$MANIFEST" 2>/dev/null | version_of)
    [ -n "$v" ] && printf '%s\n' "$v" > "$DATA/latest.tmp" && mv -f "$DATA/latest.tmp" "$DATA/latest"
  fi
  rm -rf "$tmp"
}

if [ "${1:-}" = refresh ]; then refresh; exit 0; fi

IN=$(cat 2>/dev/null)
EVENT=$(sed -n 's/.*"hook_event_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' <<<"$IN" | head -n 1)
SID=$(sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' <<<"$IN" | head -n 1)

case "$EVENT" in
  SessionStart)
    now=$(date +%s); last=$(stat -c %Y "$DATA/checked" 2>/dev/null || echo 0)
    if [ $((now - last)) -ge "$INTERVAL" ]; then
      touch "$DATA/checked"
      find "$DATA/told" -type f -mtime +7 -delete 2>/dev/null
      if [ "${OPSCI_UPDATE_SYNC:-}" = 1 ]; then refresh  # tests
      else (setsid bash "${BASH_SOURCE[0]}" refresh </dev/null >/dev/null 2>&1 &) ; fi
    fi
    ;;
  Stop)
    [ -n "$SID" ] && [ ! -e "$DATA/told/$SID" ] || exit 0
    cached=$(head -n 1 "$DATA/latest" 2>/dev/null | clean_version)  # also a file an older version wrote
    local_mkt=$(version_of < "$BASE/marketplaces/$MKT/$MANIFEST" 2>/dev/null)
    latest=$(newest "$cached" "$local_mkt")
    running=$(plugin_version "$ROOT")  # the version this session loaded
    [ -n "$latest" ] && [ -n "$running" ] && [ "$latest" != "$running" ] || exit 0
    [ "$(newest "$running" "$latest")" = "$latest" ] || exit 0
    installed=$(newest "$running" "$(on_disk)")
    touch "$DATA/told/$SID"
    if [ -n "${PLUGIN_ROOT:-}" ]; then  # Codex sets PLUGIN_ROOT; Claude Code does not
      how='run `codex plugin marketplace upgrade open-science`, re-add each plugin with `codex plugin add <plugin>@open-science`, and restart Codex'
      load='restarting Codex loads it'
    else
      how='run `claude plugin update <plugin>@open-science` for each installed plugin and start a new session'
      load='a new session loads it'
    fi
    if [ "$(newest "$installed" "$latest")" = "$installed" ] && [ "$installed" != "$running" ]; then
      msg="open-science $installed is installed; this session runs $running, and $load. No need to act now. The framework's docs/updating.md has the steps for opsci and each project; CHANGELOG.md says what changed."
    else
      msg="open-science $latest is available (installed: $installed). No need to act now. To update when convenient, $how. The framework's docs/updating.md has the steps for opsci and each project; CHANGELOG.md says what changed."
    fi
    printf '{"systemMessage": "%s"}\n' "$msg"
    ;;
esac
exit 0
