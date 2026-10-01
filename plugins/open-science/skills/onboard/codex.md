# Onboarding with Codex

This is the Codex path through onboarding. Keep the same research choices and privacy
rules as the shared skill. Do not edit Claude Code settings, move Claude skills, or
change the user's tmux setup merely to install Codex support.

1. Check `codex --version`, `codex plugin marketplace list`, `codex plugin list`,
   `opsci --help`, and the available git identity. Ask only for missing choices:
   project management, context/handoff, publishing, and optionally SLURM recovery.
   Ask the intended project directory and notification preference (Notion, Slack,
   or local files). The descriptions in `explanations.md` explain the research components;
   their Claude-specific installation and session controls apply only to Claude Code.
2. Install the selected components with `codex plugin add <plugin>@open-science`.
   If the marketplace is missing, first run
   `codex plugin marketplace add mhycheung/open-science`. Install
   `open-science-project` before `open-science-context`; do not assume dependencies
   are installed automatically. The other component is `open-science-publish`.
   Restart Codex after installation. If this CLI lacks `plugin`, report the required
   upgrade rather than modifying another agent's configuration.
3. Install `opsci` from the same framework release, if absent. From a checkout use
   `pip install -e tools`; otherwise use the repository URL with `#subdirectory=tools`.
   Keep a checkout for the project template: a cached plugin need not include `template/`.
4. Review the installed hook commands with the user. Codex `/hooks` must trust the
   current definitions before they run. Never mark them trusted on the user's behalf
   or bypass trust during ordinary onboarding. Keep existing user hooks and settings.
5. Project setup uses `open-science-project:new-project` or, for an existing layout,
   `open-science-project:update-from-template`. Both agents use the same `AGENTS.md`,
   context files, plans, citations, and results. Codex uses `config/codex.md` and
   `.codex/agents/`; existing `CLAUDE.md` and `.claude/` remain intact.
6. For context/handoff, load the Codex reference in
   `open-science-context:context-management`. Only offer features that reference supports.
   Ask whether jumps should be `all`, `wait`, or `off`, preserving an existing choice.
   Explain the tmux, trusted-hook, and writable-state requirements before enabling jumps.
   Offer a launch command with `OPSCI_JUMPS=<choice>` and the narrow state directory
   passed through `--add-dir`; preserve the user's model, sandbox and approval settings.
   Without that setup, offer manual handoff from a named context file.
7. For notifications and publication credentials, keep the shared setup choices and
   storage under `~/.config/opsci/`. The user runs `scripts/secret_file.sh` from this
   plugin in their own terminal. Never read or display credentials. Notion uses
   `opsci notion check`, `opsci notion enable`, and `opsci notion init` as documented
   in `open-science-project:notion`. Slack uses `opsci notify --backend slack` only
   when the user authorizes a message. Do not send test messages unasked.
8. Report what was installed, what awaits hook trust or credentials, and the next
   project command. Do not claim configuration is active just because files exist.

For upgrades, run `codex plugin marketplace upgrade open-science`, then re-add the
selected plugins and restart Codex. Use each CLI's `--help` if its update interface
differs. Recheck hook trust after an update. Existing Claude Code installations stay intact.
