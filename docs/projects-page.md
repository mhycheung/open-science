# Personal projects page

An optional extra: a single web page, on your personal GitHub Pages site, that lists your
projects with a short description, tags and links, and lets visitors filter and sort them.
It is two files in `extras/projects-page/` of the framework repository. It has no plugin and
works on its own; `opsci` is needed only to check the files.

| file | what |
|---|---|
| `index.html` | the page: layout, style and script in one file |
| `projects.yaml` | the list of projects; the only file you normally edit |
| `AGENTS.md` | rules for AI agents that edit the page |

The page runs in the browser and needs no build step and no server-side code, so any static
host works.

## Add it to your GitHub Pages site

1. If you have no personal site yet, make a public repository on github.com named exactly
   `<username>.github.io`; GitHub serves it as your site.
2. In that repository, make a directory for the page, for example `projects/`.
3. Copy `index.html` and `projects.yaml` into it. Copy `AGENTS.md` too if agents work in
   that repository.
4. Replace the example projects in `projects.yaml` with yours, and write the `intro` and
   each `description` yourself.
5. Check the files: `opsci projects-page check projects/`.
6. Commit and push. The page is then at `https://<username>.github.io/projects/`.

This works with or without Jekyll (the GitHub Pages default): Jekyll copies both files
unchanged because neither starts with a `---` front-matter block.

`open-science:onboard` can do steps 2 to 5 for you: it asks for your site's folder, copies the
files after a yes, and helps you write `projects.yaml`. It commits in the site repository only
after a yes and never pushes. `open-science-project:new-project` adds a new project to the
list only if you keep one and ask.

## `projects.yaml`

```yaml
title: Projects
intro: ""                     # written by you; empty shows no introduction
projects:
  - title: Tide pool census   # required
    description: ""           # required key, written by you; may stay empty
    tags: [ecology, field-work, data]   # required list, may be empty
    status: active            # required: active, paused, finished or archived
    updated: 2026-08          # optional: YYYY, YYYY-MM or YYYY-MM-DD
    links:                    # optional
      repo: https://example.org/tide-pool-census       # http or https
      site: https://example.org/tide-pool-census/site  # http or https
      doi: 10.5281/zenodo.0000001
```

For text over several lines use `|` and indent the lines below it. Put a value in quotes if
it contains `: ` or starts with a quote, `[`, `{`, `&`, `*`, `!`, `|` or `>`. The page reads
the file with its own small reader, which accepts only part of YAML; the check refuses
anything that reader cannot read.

The `repo`, `site` and `doi` links are where a published project's outputs go: the public
repository, the project website, and the Zenodo DOI (see
[Publishing and the filter](publishing.md) and [Zenodo releases](zenodo.md)).

## Descriptions are written by you

The `description` of each project and the page's `intro` ship empty. You write them, in your
own words. An agent may add or edit titles, tags, status, dates and links, and may point out
that a description is missing, but must not write, draft or rewrite a description or the
intro (`extras/projects-page/AGENTS.md`).

## `opsci projects-page check`

```bash
opsci projects-page check [DIR]     # DIR holds index.html and projects.yaml (default .)
```

It refuses:

- a missing `index.html` or `projects.yaml`;
- in `projects.yaml`: a missing required field, an unknown key, a bad `status`, `updated`,
  URL or DOI, a duplicate title or tag, and YAML the page cannot read (anchors, flow mappings,
  folded text, unclosed quotes or lists);
- in `index.html`, the banned style elements: a cream or off-white background, italic accent
  words in headlines, numbered "01/02/03" section labels, monospace labels, and pill-shaped
  buttons.

## What the page does

- Click tags to filter. "any selected tag" shows projects with at least one of them; "all
  selected tags" shows projects with every one.
- Sort by most matching tags (then most recent), most recently updated, title, or status.
- The current filter and sort are kept in the address (for example
  `#tags=ecology,data&mode=all`), so a filtered view can be linked to.

To look at it locally, serve the directory (browsers do not let a page opened from disk read
`projects.yaml`): `python3 -m http.server` in the page's directory, then open
<http://localhost:8000/>.
