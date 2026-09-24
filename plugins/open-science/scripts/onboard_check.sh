#!/usr/bin/env bash
# What this machine already has, for open-science:onboard. Prints one key=value line per
# check, so the skill asks only about what is missing. It never prints the contents of a
# credentials file: for those it reports only missing / ok / the problem with the file.
#
#   onboard_check.sh [--no-network]     # --no-network skips the GitHub SSH test
set -u

NETWORK=1
[ "${1:-}" = "--no-network" ] && NETWORK=0

CFG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
OPSCI_CFG="${XDG_CONFIG_HOME:-$HOME/.config}/opsci"

have() { command -v "$1" >/dev/null 2>&1; }
kv() { printf '%s=%s\n' "$1" "$2"; }

# tmux: installed, this session inside it, mouse support
if have tmux; then
    kv tmux "$(tmux -V 2>/dev/null | awk '{print $2}')"
    mouse=unknown
    if [ -n "${TMUX:-}" ]; then
        mouse=$(tmux show -gv mouse 2>/dev/null || echo unknown)
    elif [ -f "$HOME/.tmux.conf" ]; then
        grep -Eq '^[[:space:]]*set(-option)?[[:space:]]+-g[[:space:]]+mouse[[:space:]]+on' \
            "$HOME/.tmux.conf" && mouse=on || mouse=off
    fi
    kv tmux_mouse "$mouse"
else
    kv tmux missing
fi
[ -n "${TMUX:-}" ] && kv in_tmux yes || kv in_tmux no

# SLURM: scheduler commands, and whether this runs inside a batch job
have sbatch && kv slurm present || kv slurm missing
kv batch_job "${SLURM_JOB_ID:-no}"
missing=""
for t in jq flock setsid sbatch squeue scancel; do have "$t" || missing="$missing${missing:+,}$t"; done
kv batch_tools "${missing:+missing:$missing}${missing:-ok}"

# Claude Code, Python, pixi, opsci
if have claude; then kv claude "$(claude --version 2>/dev/null | awk '{print $1}')"; else kv claude missing; fi
py=missing
for p in python3 python; do
    if have "$p" && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
        py="$("$p" -c 'import platform; print(platform.python_version())')"; break
    fi
done
kv python311 "$py"
have pixi && kv pixi present || kv pixi missing
have opsci && kv opsci present || kv opsci missing

# git identity (the author of every commit, public once a project is published)
kv git_name "$(git config --global user.name 2>/dev/null || true)"
kv git_email "$(git config --global user.email 2>/dev/null || true)"

# plugins: one line each, installed / not-installed
for p in open-science-publish open-science-project open-science-context slurm-resurrect; do
    if grep -q "\"$p@" "$CFG/plugins/installed_plugins.json" 2>/dev/null; then
        kv "${p//-/_}" installed
    else
        kv "${p//-/_}" not-installed
    fi
done

# personal skills with the same name as a framework skill (they win over the plugin's).
# The plugins are cached apart once installed, so the names are listed here;
# tests/test_onboard.py checks the list against the skill directories of the plugins.
SKILL_NAMES="onboard publish zenodo-release new-project new-task context-files migrate-project
update-from-template context-management continue-context advise-with-context"
same=""
for n in $SKILL_NAMES; do
    [ -e "$CFG/skills/$n" ] && same="$same${same:+,}$n"
done
kv same_name_skills "${same:-none}"

# credentials: presence and permissions only
private_state() {  # $1 path, $2 wanted mode
    local f=$1 want=$2 mode owner
    [ -e "$f" ] || { echo missing; return; }
    owner=$(stat -c %u "$f"); mode=$(stat -c %a "$f")
    [ "$owner" = "$(id -u)" ] || { echo not-owned; return; }
    [ -L "$f" ] && { echo symlink; return; }
    [ "$mode" = "$want" ] && echo ok || echo "mode-$mode"
}
kv secret_dir "$(private_state "$OPSCI_CFG" 700)"
for f in slack.env zenodo-sandbox.token zenodo.token; do
    kv "secret_$f" "$(private_state "$OPSCI_CFG/$f" 600)"
done
if grep -q 'Read(~/.config/opsci' "$CFG/settings.json" 2>/dev/null; then
    kv deny_rule present
else
    kv deny_rule absent
fi

# GitHub over SSH: exit 1 with "successfully authenticated" means the key works
if [ "$NETWORK" = 1 ] && have ssh; then
    # BatchMode: never prompts, and never adds a host key to known_hosts on its own.
    out=$(timeout 15 ssh -T -o BatchMode=yes -o ConnectTimeout=10 git@github.com 2>&1 </dev/null)
    case "$out" in
        *"successfully authenticated"*) kv github_ssh "ok:$(sed -n 's/^Hi \([^!]*\)!.*/\1/p' <<<"$out")" ;;
        *"Host key verification failed"*) kv github_ssh fail:host-key-unknown ;;
        *"Permission denied"*) kv github_ssh fail:no-key-accepted ;;
        *) kv github_ssh fail:no-connection ;;
    esac
else
    kv github_ssh skipped
fi

# slurm-resurrect state directory: must be on a filesystem the compute nodes share
sd="${RR_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/slurm-resurrect}"
p="$sd"; while [ ! -e "$p" ] && [ "$p" != / ]; do p=$(dirname "$p"); done
kv rr_state_dir "$sd"
kv rr_state_fs "$(stat -f -c %T "$p" 2>/dev/null || echo unknown)"
