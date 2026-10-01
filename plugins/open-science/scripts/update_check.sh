#!/usr/bin/env bash
# Hook of the open-science plugin (Claude Code and Codex): tell the user, once per session,
# that a newer open-science release exists. The agent is never told and never asked.
#
#   SessionStart   if the last check is older than a day, look up the framework's newest
#                  release in the background (the version in the open-science plugin's
#                  manifest on the repository's default branch). Prints nothing.
#   Stop           if the release found is newer than this plugin's version, and this
#                  session has not shown the notice yet, show it to the user (a
#                  `systemMessage`, which both agents show to the user and not to the
#                  model). Later turns of the same session show nothing.
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

version_of() {  # the "version" of a plugin.json on stdin
  sed -n 's/^[[:space:]]*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1
}

repo_url() {
  if [ -n "${OPSCI_UPDATE_REPO:-}" ]; then echo "$OPSCI_UPDATE_REPO"; return; fi
  # an installed plugin is <base>/cache/<marketplace>/<plugin>/<version>; its marketplace
  # checkout is <base>/marketplaces/<marketplace>
  local mkt base url
  mkt=$(basename "$(dirname "$(dirname "$ROOT")")")
  base=$(dirname "$(dirname "$(dirname "$(dirname "$ROOT")")")")
  url=$(git -C "$base/marketplaces/$mkt" remote get-url origin 2>/dev/null) && [ -n "$url" ] \
    && { echo "$url"; return; }
  echo "$DEFAULT_REPO"
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
    [ -n "$SID" ] && [ ! -e "$DATA/told/$SID" ] && [ -s "$DATA/latest" ] || exit 0
    latest=$(head -n 1 "$DATA/latest")
    installed=$(version_of < "$ROOT/.claude-plugin/plugin.json" 2>/dev/null)
    [ -n "$installed" ] || installed=$(version_of < "$ROOT/.codex-plugin/plugin.json" 2>/dev/null)
    [ -n "$installed" ] && [ "$latest" != "$installed" ] || exit 0
    [ "$(printf '%s\n%s\n' "$installed" "$latest" | sort -V | tail -n 1)" = "$latest" ] || exit 0
    touch "$DATA/told/$SID"
    if [ -n "${PLUGIN_ROOT:-}" ]; then  # Codex sets PLUGIN_ROOT; Claude Code does not
      how='run `codex plugin marketplace upgrade open-science`, re-add each plugin with `codex plugin add <plugin>@open-science`, and restart Codex'
    else
      how='run `claude plugin update <plugin>@open-science` for each installed plugin and start a new session'
    fi
    msg="open-science $latest is available (installed: $installed). No need to act now. To update when convenient, $how. The framework's docs/updating.md has the steps for opsci and each project; CHANGELOG.md says what changed."
    printf '{"systemMessage": "%s"}\n' "$msg"
    ;;
esac
exit 0
