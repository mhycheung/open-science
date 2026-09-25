# Unpublished nodes in the public map

The published `map/graph.md` and `map/dead_ends.md` name every node that is not
hard-private. A node whose files are not exported (a soft-private task, a brainstorm idea,
a public node outside the manifest) appears without a link, with its title, summary, status
and edges. Hard-private nodes and their edges never appear, and need nothing here.

## The judgement

The public map should tell readers that private work exists, not what it is. For each
unpublished node in the report section `## Unpublished nodes in the public map`, ask: could
a reader reconstruct the work from its title, its summary and the nodes its edges connect it
to? "Private cross-check" gives nothing away. "Acme detector gain at 3.2 kV, March run"
gives the work away.

Choose how much to hide, node by node. Leave a node as it is when it says nothing specific.
Rewrite its title and summary in more general words when the node alone is too specific.
Replace several connected nodes with one node when together they outline the work, for
example a chain of calibration steps. Hide no more than needed.

## `publish/map_overrides.yaml`

Committed in the private repo, never exported. Only the published copy of the map changes.

```yaml
groups:                        # each group replaces its members in the public map
  - id: private-calibration    # a new id, not used by any node
    title: Private calibration work
    summary: Several private studies of the detector calibration.
    members: [t05-gain, t06-drift]   # at least two unpublished nodes
    # status: active           # optional
    # type: task               # optional; default task
nodes:                         # single nodes shown with another title and summary
  t07-residuals:
    title: A private cross-check
    summary: A cross-check of the fit.
```

- Edges to a member point to its group; edges between members disappear.
- A group's status is its members' status if they agree, otherwise `active` if any member
  is active, otherwise `done`, unless `status` sets it.
- The `map-overrides` check refuses a member or node that is published, hard-private or
  unknown; a group with fewer than two members; an id already in use; a node in two
  entries; a missing or multi-line title or summary; an unknown key. Remove an entry when
  its node is removed or published.
- The new title and summary go into the public repo: they must not name hard-private
  material. The `private-content` check reads the rebuilt map like any exported file.
