# Personal projects page

A single web page that lists your projects, with a tag filter and sorting. It runs in the
browser and needs no build step and no server-side code, so any static host works,
including GitHub Pages.

| file | what |
|---|---|
| `index.html` | the page: layout, style and script in one file |
| `projects.yaml` | the list of projects; the only file you normally edit |
| `AGENTS.md` | rules for AI agents that edit the page |

## Add it to a GitHub Pages site

1. In the repository of your site (for a user site, `<username>.github.io`), make a
   directory for the page, for example `projects/`.
2. Copy `index.html` and `projects.yaml` into it. Copy `AGENTS.md` too if agents work in
   that repository.
3. Replace the example projects in `projects.yaml` with yours, and write the `intro` and
   each `description` yourself.
4. Check the file: `opsci projects-page check projects/`.
5. Commit and push. The page is then at `https://<username>.github.io/projects/`.

This works with or without Jekyll (the GitHub Pages default): Jekyll copies both files
unchanged because neither starts with a `---` front-matter block. Other static hosts need
the same two files side by side.

## Look at it locally

Browsers do not let a page opened from disk read `projects.yaml`, so serve the directory:

```bash
cd projects/
python3 -m http.server
```

and open <http://localhost:8000/>.

## What the page does

- Click tags to filter. "any selected tag" shows projects with at least one of them; "all
  selected tags" shows projects with every one.
- Sort by most matching tags (then most recent), most recently updated, title, or status.
- The current filter and sort are kept in the address (for example
  `#tags=ecology,data&mode=all`), so a filtered view can be linked to.
- A project or intro with an empty description shows no description text.
