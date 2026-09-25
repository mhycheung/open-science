---
name: onboard
description: Set up the open-science framework for a user - check what this machine already has, ask which of the three components they want (project management, context management, publishing) and whether they want the optional extras (projects list, SLURM resurrection), install those plugins, set up tmux for context management (mouse settings, one pane per task, a tmux batch job on a SLURM cluster), and set up GitHub access, Zenodo and Slack tokens safely. Use when the user asks to onboard, install, set up or configure open-science, or to add a component later.
---

# Onboard

The user may know nothing about this framework, Claude Code plugins, git hosting or tmux.
Every question and option uses the plain-language texts in `explanations.md` (next to this
file; read it now). Put an option's text in its `description`; for a single-select
question also put the longer text in its `preview`. Do not shorten the texts into jargon.

**Rules for the whole session:**
- **Ask only what the checks cannot answer.** A check that says all is well skips its question.
- **No change to the user's settings without a yes to that change**: git config, Claude
  settings, `~/.tmux.conf`, file modes, moving skills. Choosing a component in Q1 is the
  yes to installing its plugin; say so in Q1.
- **Tokens never pass through this chat, a command line, an environment variable or
  shell history.** Never ask for one, never read a credentials file, never print one. If the
  user pastes a token anyway, tell them to revoke it on the service's site and make a new one.
- Never use `!` for a token command: its output lands in the chat.

## Procedure

1. **Intro**: say the "Intro" text.

2. **Silent checks.** Run `bash "${CLAUDE_PLUGIN_ROOT}/scripts/onboard_check.sh"` and keep
   its `key=value` lines. Do not show them; use them to skip questions. It prints only
   `missing`/`ok`/the problem for credentials files, never their contents.

3. **Q0, commit identity.** Always ask, with `git_name` and `git_email` filled in. If they
   are empty, or the user picks "different", ask for name and email in plain text, then
   `git config --global user.name "<name>"` and `git config --global user.email "<email>"`.

4. **Q1, components, and Q2, optional extras** (both multi-select, in one widget call; the
   options and their order are in `explanations.md`). The SLURM extra in Q2 follows the
   three cases in `explanations.md` (in a job, cluster outside a job, no SLURM). Context management without project management: ask the
   follow-up in `explanations.md`. Nothing chosen in either: stop here.

5. **Install the chosen plugins.** Tell the user the commands, then run them:

   | component | plugin |
   |---|---|
   | project management | `open-science-project` |
   | context management | `open-science-context` (installs `open-science-project` with it) |
   | publishing | `open-science-publish` |
   | SLURM resurrection (extra) | `slurm-resurrect` |

   `claude plugin install <plugin>@open-science`, skipping any the check reports
   `installed`. The projects list has no plugin: it is two files (step 7, branch 3).
   New plugins load only in a new Claude Code session; say so in the summary.

6. **The `opsci` command**, needed by project management, context management and publishing
   (the projects list works without it). If the check says `opsci=missing`, it needs Python
   3.11 or later (`python311`) or pixi. Ask where to install, then run
   `pip install "git+<repository URL>#subdirectory=tools"`, with the URL of this plugin's
   marketplace (`claude plugin marketplace list`). Verify with `opsci --help`.

7. **One branch per chosen component**, in this order, with the texts in `explanations.md`:
   - **Branch 1, context management.** Context management requires Claude Code inside tmux;
     say so. Configure what is missing; skip what the checks say is done.
     `tmux=missing`: tell the user to ask their system administrator, or to install it with
     their package manager; the component does not work without it, so skip 1a-1c.
     - 1a, tmux settings, only if `tmux_conf_missing` is not `none`: show the lines of the
       recommended block in `explanations.md` for the settings it names, and offer to append
       them to `~/.tmux.conf` (one yes for the block; back up an existing file to
       `~/.tmux.conf.bak-<date>` first). If `in_tmux=yes`, then run
       `tmux source-file ~/.tmux.conf`.
     - 1b, the layout: say the "1b" text (one session, one window per project, one pane per
       task). If `in_tmux=yes`, offer to rename the current window after the project the
       user will work on first (`tmux rename-window <name>`).
     - 1c, where tmux runs, only if `in_tmux=no`. With `slurm=present` and `batch_job=no`:
       say the "1c, cluster" text and offer to write `~/tmux-job.sh` from the job script in
       `explanations.md`, with the account, partition and time limit the user gives
       (`sbatch` it only after a yes), then give the connect steps of that text. Otherwise say the "1c, local" text.
     - 1e, session names, only if `session_names=off`: ask with the "1e" text. After a yes,
       set `"OPSCI_SESSION_NAMES": "1"` in the `env` object of `<config>/settings.json`
       (`<config>` = `$CLAUDE_CONFIG_DIR` or `~/.claude`; create `env` if absent, keep every
       other key). It takes effect in new Claude Code sessions.
     - 1d, only if `same_name_skills` is not `none`: move the named directories to
       `<config>/skills-archive/` after a yes. Rename the archive folder if one is already
       there; never delete anything.
   - **Branch 2, publishing.** Say the branch's opening text (nothing goes public without an
     explicit yes). 2a GitHub username: take it from `github_ssh=ok:<name>` if
     present, else ask. 2b only if `github_ssh` is not `ok`: SSH key (help make one with
     `ssh-keygen -t ed25519`, then the user adds `~/.ssh/id_ed25519.pub` on github.com →
     Settings → SSH keys; `fail:host-key-unknown` means the user must connect once by hand
     and compare GitHub's published fingerprint) or HTTPS token (git's credential store;
     the user enters it at git's own prompt, never here). 2c, 2d as in the texts. 2e: say
     it, no question. 2f Zenodo: tokens via step 9; ORCID and affiliation go in each
     project's `CITATION.cff`, so only tell the user where.
   - **Branch 3, projects list (extra).** 3a. If the user has the site: ask for its folder, copy
     `extras/projects-page/index.html` and `extras/projects-page/projects.yaml` from the framework repo
     into `<site>/projects/` after a yes, then help write `projects.yaml` (their intro and
     descriptions, not yours); check with `opsci projects-page check <site>/projects/` if
     `opsci` is installed. Commit in the site repo only after a yes; never push. If they have
     no site, give the steps from the text and stop the branch there. If branch 2 was not
     chosen, run its 2b too: the site is pushed with git.
   - **Branch 4, SLURM resurrection (extra).** It is for development on a compute node of a
     SLURM cluster; say so. Check `batch_tools=ok` (else say which are missing),
     and that `rr_state_dir` is shared by the compute nodes: `rr_state_fs` `nfs`, `lustre`
     or `gpfs` is; `tmpfs` or a path under `/tmp` is not (explain `RR_STATE_DIR`); for
     anything else ask the user. 4a: Claude must run in tmux inside a batch job; if
     `batch_job=no` and 1c did not already write it, offer the job script of 1c (`sbatch` it
     only after a yes). 4b: ask how they start Claude; if not plain `claude`, they will run
     `set launch_cmd <command>`. 4c, 4d with the texts; 4e is not a question. 4g: **you cannot register.**
     Give the user the exact line to type in a Claude pane of that tmux session, e.g.
     `/slurm-resurrect:resurrect register --permission-mode acceptEdits --remote-control off`
     (the mode chosen in 4c: `acceptEdits`, `auto`, `bypassPermissions` or `manual`),
     and tell them it shows a warning the first time and registers the second time.

8. **Notifications** (if any of project management, context management, publishing or SLURM
   resurrection was chosen): Files or Slack. Files needs nothing. Slack: follow "Slack
   setup" in `explanations.md`, one step at a time, waiting for the user after each (the
   manifest is in `docs/notify.md` in the framework repo). Always ask which channel (step 2);
   never pick one yourself. Its step 7 is step 9 below with `slack`. Its step 8: write
   `notify: {backend: slack}` into `~/.config/opsci/config.yaml` after a yes, then send
   `opsci notify "open-science onboarding: test message"` and ask whether it arrived in
   the chosen channel. With
   Slack and branch 4, the user may route resurrection notices there by typing
   `/slurm-resurrect:resurrect set-notify <path to opsci> notify "$RR_MSG"` (no outer quotes;
   `<path to opsci>` from `command -v opsci`, since the batch job's PATH may differ). Not with
   Files: the message would land in whatever directory the resurrection runs in.

9. **Tokens** (Slack, Zenodo sandbox, Zenodo production). Say the "Tokens" text first. Give
   the user this command to run **in a separate terminal of their own**, with the plugin path
   resolved to the real absolute path (print it; `echo "${CLAUDE_PLUGIN_ROOT}"`):
   `bash <plugin dir>/scripts/secret_file.sh slack|zenodo-sandbox|zenodo`.
   It refuses to run inside Claude Code. Wait until the user says it printed `ok`, then check:
   Zenodo sandbox `opsci zenodo check-token`, production `opsci zenodo check-token --production`
   (a read-only request; prints ok or the error), Slack the test message of step 8.
   Scopes: Zenodo `deposit:write` and `deposit:actions`; Slack `chat:write`, `files:write`.
   - If `secret_dir` is `mode-*`: offer `chmod 700 ~/.config/opsci`. A `secret_*` file with
     `mode-*`: offer `chmod 600` on it. `symlink` or `not-owned`: explain; change nothing.
   - If `deny_rule=absent` and a token was set up: offer to add
     `"Read(~/.config/opsci/**)"` to `permissions.deny` in `<config>/settings.json`
     (`<config>` = `$CLAUDE_CONFIG_DIR` or `~/.claude`). Say plainly that it stops
     accidental reads by the agent's Read tool, and that it is not a wall: a shell command
     can still read the file.

10. **Summary.** Two lists: what was set up (with each check's result), and what is left for
    the user by hand (GitHub steps, the resurrect `register` line, restarting Claude Code so
    new plugins load, anything they postponed). Name the skills they can now use, full
    names only: `open-science-project:new-project`, `open-science-project:migrate-project`,
    `open-science-publish:publish`, `open-science-publish:zenodo-release`,
    `open-science-context:continue-context`. Running `open-science:onboard` again adds components.
