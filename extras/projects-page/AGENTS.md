# Instructions for agents working on this page

This directory is a personal projects page: `index.html` (the page) and `projects.yaml`
(the data). Read `README.md` for how it is published.

## Front-end style

Do not use a cream or off-white background, italic accent words in headlines, numbered "01/02/03" section labels, monospace labels, or pill-shaped buttons.

Keep the page plain: white background, one sans-serif font, square or slightly rounded
controls. After any change to `index.html`, run `opsci projects-page check <this directory>`;
it refuses each of the elements above.

## Descriptions are written by the human

The `description` of each project and the page's `intro` ship empty. The human writes them,
in their own words. An agent may add or edit titles, tags, status, dates and links, and may
point out that a description is missing, but must not write, draft or rewrite a description
or the intro.

## Data

- Edit `projects.yaml`, not the HTML, to change what the page lists. The fields are listed
  at the top of that file.
- The page reads `projects.yaml` with its own small reader, which accepts only part of
  YAML. `opsci projects-page check` refuses anything it cannot read.
