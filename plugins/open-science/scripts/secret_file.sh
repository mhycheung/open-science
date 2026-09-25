#!/usr/bin/env bash
# Create or edit one credentials file safely. Run it yourself, in your own terminal, never
# through an agent: the token must never pass through a chat, a command line or shell
# history.
#
#   secret_file.sh slack            # ~/.config/opsci/slack.env    (SLACK_TOKEN, SLACK_CHANNEL)
#   secret_file.sh zenodo-sandbox   # ~/.config/opsci/zenodo-sandbox.token
#   secret_file.sh zenodo           # ~/.config/opsci/zenodo.token (production)
#   secret_file.sh notion           # ~/.config/opsci/notion.env   (NOTION_TOKEN)
#
# The directory is made mode 700 and the file mode 600 (umask 077 from the start, so the file
# is never readable by others, not even for a moment). The file opens in $VISUAL, $EDITOR,
# nano or vi. Afterwards the file's shape is checked; only "ok" or the problem is printed,
# never the token.
set -euo pipefail
umask 077

die() { echo "secret_file: $*" >&2; exit 1; }

if [ -n "${CLAUDECODE:-}" ]; then
    die "this was started from inside Claude Code. Run it in your own terminal, so the token never passes through the agent."
fi

kind=${1:-}
dir="${XDG_CONFIG_HOME:-$HOME/.config}/opsci"
case "$kind" in
    slack)          file="$dir/slack.env" ;;
    zenodo-sandbox) file="$dir/zenodo-sandbox.token" ;;
    zenodo)         file="$dir/zenodo.token" ;;
    notion)         file="$dir/notion.env" ;;
    *) die "usage: secret_file.sh slack|zenodo-sandbox|zenodo|notion" ;;
esac

mkdir -p "$dir"
[ -L "$dir" ] && die "$dir is a symlink; use a real directory"
[ "$(stat -c %u "$dir")" = "$(id -u)" ] || die "$dir is not owned by you"
chmod 700 "$dir"

if [ -L "$file" ]; then die "$file is a symlink; remove it first"; fi
if [ -e "$file" ]; then
    [ "$(stat -c %u "$file")" = "$(id -u)" ] || die "$file is not owned by you"
    chmod 600 "$file"
else
    if [ "$kind" = slack ]; then
        cat > "$file" <<'EOF'
# Slack credentials for opsci notify. Keep this file private (mode 600); never commit it.
# Bot User OAuth Token of your own Slack app (starts with xoxb-):
SLACK_TOKEN=
# ID of the channel to post in (starts with C; the app must be invited to the channel):
SLACK_CHANNEL=
EOF
    elif [ "$kind" = notion ]; then
        cat > "$file" <<'EOF'
# Notion credentials for opsci notion and opsci notify. Keep this file private (mode 600);
# never commit it. Internal Integration Secret of your own Notion integration (starts ntn_):
NOTION_TOKEN=
EOF
    else
        : > "$file"   # the token only: no comments, no other text
    fi
fi

editor=${VISUAL:-${EDITOR:-}}
if [ -z "$editor" ]; then
    for e in nano vi; do command -v "$e" >/dev/null 2>&1 && { editor=$e; break; }; done
fi
[ -n "$editor" ] || die "no editor found; set EDITOR"
echo "Opening $file in $editor. Paste the token, save and quit."
$editor "$file"
chmod 600 "$file"   # an editor may have written a new file

# Check the shape. Print problems, never values.
problems=()
if [ "$kind" = slack ]; then
    tok=$(sed -n 's/^[[:space:]]*\(export[[:space:]]\+\)\?SLACK_TOKEN[[:space:]]*=[[:space:]]*//p' "$file" | tail -1 | tr -d "\"' \r")
    chan=$(sed -n 's/^[[:space:]]*\(export[[:space:]]\+\)\?SLACK_CHANNEL[[:space:]]*=[[:space:]]*//p' "$file" | tail -1 | tr -d "\"' \r")
    [ -n "$tok" ] || problems+=("SLACK_TOKEN is empty")
    [ -z "$tok" ] || [[ "$tok" == xoxb-* ]] || problems+=("SLACK_TOKEN does not start with xoxb- (use the Bot User OAuth Token)")
    [[ "$chan" =~ ^[CG][A-Z0-9]{6,}$ ]] || problems+=("SLACK_CHANNEL is not a channel ID (C followed by letters and digits)")
    unset tok chan
elif [ "$kind" = notion ]; then
    tok=$(sed -n 's/^[[:space:]]*\(export[[:space:]]\+\)\?NOTION_TOKEN[[:space:]]*=[[:space:]]*//p' "$file" | tail -1 | tr -d "\"' \r")
    [ -n "$tok" ] || problems+=("NOTION_TOKEN is empty")
    [ -z "$tok" ] || [[ "$tok" =~ ^(ntn|secret)_[A-Za-z0-9]{20,}$ ]] || problems+=("NOTION_TOKEN does not look like an integration secret (ntn_ followed by letters and digits)")
    unset tok
else
    n=$(tr -s '[:space:]' '\n' < "$file" | grep -c . || true)
    len=$(tr -d '[:space:]' < "$file" | wc -c)
    [ "$n" = 1 ] || problems+=("the file must hold exactly one token and nothing else (found $n words)")
    [ "$n" != 1 ] || [ "$len" -ge 20 ] || problems+=("the token is only $len characters long; check you pasted all of it")
fi

if [ ${#problems[@]} -gt 0 ]; then
    printf 'problem: %s\n' "${problems[@]}" >&2
    echo "Run the same command again to fix it." >&2
    exit 1
fi
echo "ok: $file (mode 600, directory mode 700)"
