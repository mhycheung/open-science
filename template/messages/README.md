# Messages

Messages to the user, one document per message. `opsci notify "<text>" [file]` writes here
when no other notify back end is configured (the default). Agents call it before a session
ends or hands over, and when something needs the user's attention.

Each file is named `YYYY-MM-DD_HHMMSS_<first-words-of-the-message>.md`. A second message in
the same second gets `-2`, `-3`, ... after the name. An attached file is copied next to the
message as `<message name>_<file name>` and linked from it (files over 50 MB are linked, not
copied). In a git worktree, messages go to the `messages/` directory of the main checkout,
so they are all in one place.

Read them in name order; delete them when done. The directory is git-ignored, apart from
this README: messages are for the user of this checkout, can contain site details (paths,
job IDs), and attachments can be large. The project record is `log/`, not this directory.

To send messages to Slack instead, see `docs/notify.md` in the framework repository.
