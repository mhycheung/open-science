# Notifications: `opsci notify`

```
opsci notify [--backend NAME] [--project-root PATH] TEXT [FILE]
```

Sends a message, and optionally one file, to the project's user. Agents call it before a
session ends or hands over, and when something needs the user. Three back ends ship:

| back end | what it does | setup |
|---|---|---|
| `file` (default) | writes each message as its own document in `messages/` | none |
| `notion` (recommended) | posts to the project's Feed in Notion and @mentions you | [Notion](notion.md) |
| `slack` | posts to a Slack channel through your own Slack app | this page |

Exit status: `0` sent; `2` misconfiguration or bad input (a missing attachment, an unknown
back end, a bad config, a missing or too-open credentials file); `3` the Slack send failed.
When the selected back end is not `file` and fails, the message is also written to
`messages/`, so it is not lost, and the command still exits non-zero.

## The file back end

This is what you get with no configuration. Each message is one markdown file:

```
messages/2026-09-23_101500_run-finished-all-good.md
messages/2026-09-23_101500_run-finished-all-good-2.md    # second message in the same second
messages/2026-09-23_101500_run-finished-all-good_fig.png # a copy of the attached file
```

- The name is the local date and time, then the first words of the message.
- A file is never overwritten; a name already taken gets `-2`, `-3`, ...
- An attached file is copied next to the message and linked from it. Files over 50 MB are
  linked where they are, not copied.
- `messages/` is at the project root. In a git worktree the messages go to the main
  checkout's `messages/`, so they are all in one place and survive removing the worktree.
- `messages/` is git-ignored except its README: messages are for the user of this checkout,
  can contain site details, and attachments can be large. The project record is `log/`.

To put messages somewhere else (for example one inbox for all your projects), set
`notify.file.dir` (relative to the project, or a `~/` path).

## Config

`opsci notify` reads the `notify:` section of two YAML files. The project file wins, key
by key:

1. user level: `~/.config/opsci/config.yaml` (or the file named by `$OPSCI_CONFIG`;
   `~/.config` is `$XDG_CONFIG_HOME` if that is set);
2. project level: `config/site.local.yaml` (git-ignored; see
   `config/site.example.yaml`). In a worktree without one, the main checkout's is used.

```yaml
notify:
  backend: slack                                   # file | slack | notion; default file
  slack:
    credentials_file: ~/.config/opsci/slack.env    # this is the default
    channel: C0123456789                           # optional; overrides SLACK_CHANNEL
    # ca_file: <CA bundle>   only if the default certificate store cannot verify slack.com
  # file:
  #   dir: messages
```

Put `backend: slack` in the user-level file to use Slack in all your projects. Neither file
may hold the token: `opsci notify` refuses a config with a key containing `token` or a value
that looks like a Slack token.

`--backend NAME` overrides the config for one call.

## Setting up Slack

You create your own Slack app in a workspace where you can install apps. It gets a bot
token that can post messages and upload files, and nothing else.

### 1. Create the app

1. Go to <https://api.slack.com/apps>, choose **Create New App**, then **From a manifest**,
   and pick your workspace.
2. Paste this manifest (YAML), then create the app:

   ```yaml
   display_information:
     name: opsci notify
   features:
     bot_user:
       display_name: opsci-notify
   oauth_config:
     scopes:
       bot:
         - chat:write
         - files:write
   settings:
     org_deploy_enabled: false
   ```

   Or create it **From scratch** and, under **OAuth & Permissions → Scopes → Bot Token
   Scopes**, add exactly these two:

   | scope | used for |
   |---|---|
   | `chat:write` | `chat.postMessage` (the text) |
   | `files:write` | `files.getUploadURLExternal` and `files.completeUploadExternal` (the file) |

   Add no other scopes. The app needs no read scopes, no user token scopes, no event
   subscriptions and no incoming webhooks. If you only ever send text you can leave out
   `files:write`; sending a file will then fail with `missing_scope`.

   How the scope names were checked: on 2026-09-23 against Slack's method reference pages
   (docs.slack.dev, `reference/methods/chat.postMessage`,
   `reference/methods/files.getUploadURLExternal` and
   `reference/methods/files.completeUploadExternal`), which list `chat:write` and
   `files:write` as the bot token scopes. The manifest fields were checked against
   `reference/app-manifest` on the same day. Neither has been tried end to end by creating
   an app; the manual test below does that.

### 2. Install it and get the token

1. **OAuth & Permissions → Install to Workspace**, and allow.
2. Copy the **Bot User OAuth Token**. It starts with `xoxb-`. Do not paste it into a chat
   with an agent, a terminal command line, or any file other than the one in step 3.
3. In Slack, create or pick the channel for notifications and invite the app to it:
   `/invite @opsci-notify`. With only `chat:write` the app can post only in channels it is
   a member of.
4. Get the channel ID (`C...`): it is shown in the channel details (click the channel
   name), and it is the last part of the channel's link (Slack screens not checked here).

### 3. Store the token in a private file

```bash
mkdir -p ~/.config/opsci && chmod 700 ~/.config/opsci
( umask 077 && ${EDITOR:-nano} ~/.config/opsci/slack.env )
chmod 600 ~/.config/opsci/slack.env
```

The file contains (in an editor, so the token never goes through a shell command line or
history):

```
SLACK_TOKEN=xoxb-...
SLACK_CHANNEL=C0123456789
```

`opsci notify` refuses the file if it is not a regular file owned by you, or if group or
others have any access to it (use mode `600`). It also refuses a credentials
file inside the project, where it could be committed.

The token is read by `opsci notify` itself and sent only in the HTTPS `Authorization`
header. It is never passed on a command line (where other users on a shared machine can
read it with `ps`), never put in the environment (where every child process and any agent
that prints its environment would see it), and never printed; error messages replace it
with `<token>`.

Do not `export SLACK_TOKEN` in `~/.bashrc` or anywhere else.

### 4. Point config at it and test

In `~/.config/opsci/config.yaml` (all projects) or the project's `config/site.local.yaml`:

```yaml
notify:
  backend: slack
```

(add `slack: {credentials_file: ...}` only if the file is not at the default path). Then:

```bash
opsci notify "hello from opsci notify"
tests/run_all --run-manual -k real_slack    # in the framework repo: sends one text and one file
```

### 5. Keep agents away from the file (recommended)

Claude Code can deny its agents read access to the file. In `~/.claude/settings.json` add
to `permissions.deny`:

```json
"Read(~/.config/opsci/slack.env)"
```

(This rule was not tested here.) This stops an
agent from reading the token by accident. It does not stop a program running
as you from reading it.

### Revoking and replacing the token

If the token may have leaked, revoke it on the app's **OAuth & Permissions** page at
<https://api.slack.com/apps>, reinstall the app to get a new token, and put the new token in
`slack.env`. Check the file mode is still `600`. (The exact button names on that page were
not checked.)

## Errors

| message | what to do |
|---|---|
| `credentials file not found` | create it (step 3), or fix `notify.slack.credentials_file` |
| `group or others have access` | `chmod 600 <file>` |
| `not_in_channel` | invite the app to the channel |
| `channel_not_found` | use the channel ID (`C...`), not its name; invite the app |
| `missing_scope` | add the scope from the table above and reinstall the app |
| `invalid_auth`, `not_authed` | the token is wrong or revoked; replace it |

## Adding a back end

A back end is a subclass of `opsci.notify.Backend` with a `name` and a
`send(text, attachment)` method that returns a one-line description of where the message
went. Register it with the `@register` decorator; `notify.backend: <name>` selects it and
`notify.<name>:` is its config section. Raise `NotifyError` for misconfiguration and
`DeliveryError` for a failed send; neither message may contain a secret. Keep credentials in
a private file checked with `check_private_file`, as the Slack back end does.
