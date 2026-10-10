# Bibliotheke

Bibliotheke is a lightweight, general-purpose knowledge graph framework: `pip install`, no server, no database, just human-readable YAML/Markdown files in your repo, versioned with git.

Every fact and edge carries three time axes, so you can travel in time through the graph:

- **Valid time** (`valid_from`, `valid_to`): when it is true in the world.
- **Knowledge time** (`known_at`): when it was first known.
- **Change time** (`changed_at`, `superseded_at`): when the record was written or replaced. Updates append a new version and never delete, so the graph can be read as it was at any earlier moment.

Bibliotheke makes no LLM calls. It gives people and coding agents (Claude Code, Codex, ...) one CLI, `bib`, to read and write the graph.

## Quickstart

```sh
bib init
bib add node person "Geoffrey Hinton"
bib add node organisation "Google"
bib add fact geoffrey-hinton field ai --valid-from 1978
bib add edge geoffrey-hinton works_at google --valid-from 2013 --valid-to 2023

bib show geoffrey-hinton                    # the current graph
bib show geoffrey-hinton --valid-at 2024    # what was true in 2024
bib show geoffrey-hinton --json             # for agents
bib view --color-by field                   # interactive graph in the terminal
```

`add fact` and `add edge` print a record id. Use it to change a record without losing history:

```sh
bib update e1a2b3c --valid-to 2024    # appends a new version
bib retract e1a2b3c                   # hides it from the current graph
bib history geoffrey-hinton           # every version, including superseded ones
```

On a graph you do not know, start with `bib types` (each node type, how many nodes, what the model says it is), then `bib browse`: a prompt that lists types, a type's nodes, a node's facts and edges, each step printed and left in the terminal; a number follows an edge, `..` goes back. `bib show` prints the ids at the far end of every edge, so the same walk works one command at a time. Everything prints and exits; only `bib view`, `bib globe` and `bib browse --tui` open a full-screen view. Other commands: `bib list`, `bib search <text>`, `bib check`, `bib model`, `bib import`, `bib fetch <url>`, `bib export`, `bib path`, `bib rank`, `bib communities`, `bib diff`. The read commands take `--valid-at`, `--known-at` and `--changed-at`, alone or combined, and `--at-commit <rev>` to read the graph as it was in a git commit. Time values are `YYYY`, `YYYY-MM`, `YYYY-MM-DD` or an ISO timestamp; `--valid-to 2023` means through the end of 2023.

## Files

A graph is a directory with a `bibliotheke.yaml` marker and one file per node at `nodes/<type>/<id>.yaml`. Node types, fact keys and relations are whatever you choose.

```yaml
id: geoffrey-hinton
type: person
name: Geoffrey Hinton
notes: ''
facts:
- id: f86accb
  key: field
  value: ai
  valid_from: 1978
  valid_to: null
  known_at: 2026-10-01
  changed_at: 2026-10-04T09:00:00Z
  superseded_at: null
edges:
- id: e2314c7
  rel: works_at
  to: google
  valid_from: 2013
  valid_to: 2023
  known_at: 2026-10-01
  changed_at: 2026-10-04T09:00:00Z
  superseded_at: null
```

## Data model

`bibliotheke.yaml` can describe what the graph holds, so that one file tells a reader (person or agent) what data is there. The node types come first, then the edges between them:

```yaml
schema_version: 1

description: >
  A multilingual vocabulary. Each node is one word in one language;
  translations link words across languages.
time: ignored              # valid_from/valid_to are not used (default: tracked)

vocabularies:
  lang: [en, sv, de]

nodes:
  word:
    description: A single word in one language.
    id: "<name>-<lang>"    # <name> or any fact key
    body: Usage notes and example sentences.
    facts:
      lang:       {type: enum, values: lang, required: true}
      definition: {type: text, required: true}
      example:    {type: text, many: true}

edges:
  translates_to:
    from: word
    to: word
    symmetric: true
    description: Same meaning in another language.
```

The same model can live in a `model/` folder instead, one file per node type, so that the folder listing on GitHub is the overview and each type is one short file to read. A type's file holds its facts and, under `edges:`, the relations that go out from it; `from` is implied by the file and only written when an edge starts from several types. Shared value lists go in `model/vocabularies.yaml`, and `bibliotheke.yaml` keeps `description`, `time` and the view settings:

```yaml
# model/word.yaml
description: A single word in one language.
name: The word as written.
id: "<name>-<lang>"
facts:
  lang:       {type: enum, values: lang, required: true}
  definition: {type: text, required: true}
edges:
  translates_to: {to: word, symmetric: true}
  defined_in:    {to: source}
```

Every node has an `id`, a `name` and `notes` whatever its type, so a type's file always says what its `name:` is, as `body:` says what its Markdown body holds. A new model started with `bib model add node` uses the folder; `bib model split` moves an existing one there. The two forms are never mixed.

Node types are lowercase with hyphens (`part-of-speech`). Fact types are `text`, `int`, `number`, `bool`, `time`, `url`, `file` and `enum`; `int: frequency` is short for `{type: int}`. An enum takes its `values` inline or names a vocabulary. A fact takes one value at a time unless it says `many: true`. Edge `from` and `to` take a type or a list of types, and a `symmetric` edge is stored once, in either direction. Edges can be `required`, and take any number of targets unless they say `many: false`. A placeholder in an `id` template is `name`, a fact key, or an edge whose target id fills it in, so `<pos>` works whether part of speech is a fact or an edge to a `part-of-speech` node.

A node type sits in valid time in one of two ways. By default it is an **interval**: a job, a membership, a file in a repository, with `valid_from` and `valid_to`. A type with `time: event` (or `bib model add node paper --event`) **happens at an instant**: a paper is published, a claim is made, and it is there from then on. Its records carry `valid_from` as *when* and no `valid_to`; `--valid-at` treats both kinds the same, but an event shows as a date (`2025-05-29`) rather than a span (`2025-05-29 → …`).

With a model in place, `bib add`, `bib update` and `bib import` refuse anything it does not allow (unknown types, keys and edges, bad values, a second value for a single-valued fact or a second target for a single-target edge), and `bib import` fills in ids from the `id` template. `bib check` (or `bib model check`, the same command from the model's side) reports hand edits that break it, as well as missing required facts and edges, and ids that do not follow the template. It prints one row per node type, those the model declares included, with how many nodes, facts and edges it has and how many nodes are consistent with the model, then every problem, then the verdict; `--json` gives the same to agents. `bib model` prints the model, and `bib model --json` gives it to agents. Without a `nodes:` section, a graph accepts anything.

### Changing the model

A model changes as a graph grows. `bib model` has subcommands for that, so a change is one command rather than a YAML edit plus a check, and each one rewrites the model files and then checks the data against the new model:

```sh
bib model add node phrase --description "Several words with one meaning"
bib model add fact word frequency int --description "Per million words"
bib model add fact word pos enum --values noun,verb,adjective --required
bib model add edge part_of word phrase --one          # one target at a time
bib model add vocabulary register formal informal slang
bib model rename fact word lang language              # rewrites every record, and the id template
bib model rename edge translates_to same_as
bib model rename node source reference                # moves nodes/source/ to nodes/reference/
bib model drop fact word example                      # refused while current records use it
bib model drop fact word example --force              # retracts them instead; they stay in history
```

A rename rewrites every version of the records that use the old name, superseded ones included, and leaves their time stamps alone: it is a change of vocabulary, not of what is known, and git keeps the old name. The model governs current records only, so a drop retracts what still uses the key or relation and history is left as it was. A node type cannot be dropped while nodes of that type exist.

For a bigger change, edit the file and let `bib` judge it:

```sh
bib model edit paper           # opens model/paper.yaml in $EDITOR, checks the result before saving
bib model diff                 # how the model changed since HEAD, and what that breaks
bib model diff v1 v2 --json    # between two commits, for agents
bib model open                 # the model/ folder on GitHub, in the browser; `open paper` for one file
```

Both classify every change as additive (`+`, nothing that was allowed is refused: a new type, an optional fact, an enum value), destructive (`-`, something that was allowed is now refused: a fact removed, a fact made required, an enum value dropped, a type changed, a template changed) or neutral (`~`, descriptions), then list the current records that break the newer model. `bib model edit` does not save a model that current records break unless you pass `--force`; `bib model diff` exits 1 in that case. Comments in the model files survive `bib model edit` but not the other subcommands, which rewrite them.

### The model under git

The model is files in git, so a change to it can be gated, reviewed, logged and versioned like code. `bib` has one command for each:

```sh
bib hook install                      # pre-commit hook: `bib check --since HEAD`, refuses destructive model changes
bib ci > .github/workflows/bibliotheke.yml   # GitHub Actions: check every PR and post the model report on it
bib model log                         # the commits that changed the model, each with what the change did
bib model version --bump              # next version from the changes since the last tag; `bib model tag` tags HEAD
bib check --since origin/main --markdown     # the model change and the data check, as one Markdown report
```

The hook runs `bib check --since HEAD --no-destructive` before every commit: a data problem or a destructive model change stops the commit. `BIB_ALLOW_DESTRUCTIVE=1 git commit` lets a destructive change through once it is meant, and `git commit --no-verify` skips the hook. The workflow does the same on pull requests against the base branch, comments the report on the PR, and fails the job on a data problem; pass `--install` to say how the runner gets `bib`. `bib model log` reads the model at each commit that touched it and summarises the diff, so the history reads as "+2: claim, made_in" rather than "bibliotheke.yaml changed". Versions follow the same classification: a destructive change bumps the major number, an additive one the minor, a neutral one the patch. `bib model version --bump` writes the number to `bibliotheke.yaml`, and after the commit `bib model tag` tags it `model-v<version>`, so consumers of the graph can pin a model version and know what a bump means.

## Bodies and files

Long-form notes live next to a node as Markdown at `nodes/<type>/<id>.md` (its *body*). `bib search` reads bodies too. A fact with the key `file` names a file relative to the graph root, such as a source PDF, and `bib check` reports it if the file goes missing.

## Batch import and export

`bib import batch.yaml` merges many nodes, facts, edges and bodies at once, which suits agents that extract a whole document in one go. Everything is checked before anything is written, and re-running a batch adds nothing twice. A node's `valid_from` is inherited by its facts and edges:

```yaml
known_at: 2026-09-20
nodes:
  - type: paper
    id: darwin-godel-machine
    name: "Darwin Gödel Machine: Open-Ended Evolution of Self-Improving Agents"
    valid_from: 2025-05-29
    facts: {year: 2025, arxiv: "2505.22954", topic: [rsi, open-endedness]}
    edges: [{rel: authored_by, to: jeff-clune}]
    body: |
      # Darwin Gödel Machine
      ...
  - {type: person, name: Jeff Clune}
edges:
  - {from: jeff-clune, rel: affiliated_with, to: ubc, valid_from: 2021}
```

`bib export` prints a Markdown table, e.g. a catalogue. Columns are `id`, `name`, `type`, a fact key, `>rel` (names of out-edge targets) or `<rel` (names of in-edge sources):

```sh
bib export --type paper --columns name,year,>authored_by,<cites --sort=-year > CATALOG.md
```

## Fetching sources

`bib fetch <url>` brings a source into the graph: one node, the files behind it under `sources/<kind>/`, and the people it names. Four kinds are known, each by its URL:

| Source | Node type | Files | Neighbours |
|---|---|---|---|
| YouTube video | `video` | the transcript: a timestamp and link per paragraph, chapters as headings | |
| arXiv paper | `paper` | the PDF, and its text as Markdown | authors, `authored_by` |
| GitHub repository | `repository` | a digest: README and the file tree | the owner, `owned_by` |
| any other page | `article` | the page's text as Markdown | |

The node's `file` fact names what to read, so an agent's job is the same whatever the kind: read the file, write the claims as a batch, `bib import` it. A transcript paragraph opens with `[12:30](https://youtu.be/ID?t=750)`, so a claim can carry the moment it was said.

```sh
bib model add source youtube            # the video type and its facts; also arxiv, github and web
bib fetch https://www.youtube.com/watch?v=aircAruvnKk
bib fetch https://arxiv.org/abs/2505.22954
bib fetch https://github.com/jennyzzt/dgm
bib fetch https://sakana.ai/dgm/ --dry-run     # the batch it would import, nothing written
```

The publish date is the node's valid time and the fetch is its knowledge time. Fetching a URL again finds the node through the source's own id (`youtube_id`, `arxiv`, `github`, `url`) and gives the facts that changed a new version, so stars and views become a history. With a model, facts, neighbours and edges the model has no place for are dropped and reported rather than refused, so a fetch fits a model that names fewer things than the source knows; `--type` picks another node type and `--id` another id. Each fetch also prints the YouTube, arXiv and GitHub links the source mentions: what to fetch next.

Captions come from YouTube through yt-dlp (`pip install 'bibliotheke[fetch]'`), uploaded ones before automatic ones, in the `--lang` language. `--transcribe whisper` downloads the audio and runs Whisper locally instead (mlx-whisper on Apple silicon, or faster-whisper), which the default, `auto`, also does when there are no captions. A paper's text comes from arXiv's HTML rendering, or for older papers from the PDF through PyMuPDF, in the same extra. GitHub needs no token for public repositories; `GITHUB_TOKEN` raises the rate limit. The fetcher is its own package, `bibliotheke.fetch`, on the public API like the repository scanner, and one module per kind, so a new kind is one module that claims its URLs and returns a node, files and neighbours.

## Graph analysis

In memory the graph is a [NetworkX](https://networkx.org/) multigraph, so the usual algorithms run on it as it was at any point in time. Three commands cover the common questions; all take the time filters, `--rel` to keep only some relations, `--json` for agents, and follow edges either way unless `--directed`:

```sh
bib path paul-cezanne pablo-picasso            # the shortest chain of edges between two nodes
bib rank --by betweenness --valid-at 1960      # degree, pagerank or betweenness centrality
bib communities --min-size 3                   # Louvain communities, largest first
```

From Python, `Graph.nx(at, rels, types)` returns a frozen `networkx.MultiDiGraph` of what is in view: one edge per record, keyed by record id, with `rel` and the time fields as attributes; a node carries `type`, `name` and one attribute per fact key. It is ready for anything NetworkX offers, including `nx.write_graphml` for Gephi and friends:

```python
import networkx as nx
from bibliotheke import At, Graph

graph = Graph.open("examples/people")
H = graph.nx(At(valid_at=1960), rels=["works_at"])
nx.write_graphml(H, "people-1960.graphml")
```

## Git

The files are the database, so git is the audit trail. Every write changes one node file, keeps the old version in it, and dumps the file deterministically, so a diff shows only what changed. Two commands read git directly, without checking anything out:

```sh
bib show geoffrey-hinton --at-commit v1.0     # the graph as committed at any revision
bib rank --at-commit HEAD~10                  # every read command takes --at-commit
bib diff HEAD~10                              # nodes, facts and edges added, changed or retracted since then
bib diff v1.0 v2.0 --json
```

`--at-commit` combines with the time filters: `--at-commit v1.0 --valid-at 1960` is what the graph, as it stood at `v1.0`, said about 1960. A graph read from a commit is read-only.

## A repository as a graph

`bib repo` reads a git repository's history and shows it as a graph of its files, directories and authors, drawn like any other graph:

```sh
bib repo .                              # this repository: a grey tree, files coloured by top-level directory
bib repo ~/src/linux --since 2.years    # a big one, recent history only
bib repo . --valid-at 2024-06           # as the repository was then
bib repo . --save graph/                # keep it as files for the other commands
```

Every commit becomes edges: `touched` from the author to each file, and `co_changed` between files changed in the same commit, so the number of edges between two nodes is how often they moved together, and `--valid-at` shows the ownership and coupling built up by that time. A file's facts (`path`, `dir`, `ext`, `commits`) run from the commit that created it to the one that deleted it; a rename adds a new `path` version. A commit's author date is valid time and its committer date is knowledge time, which is why a rebased commit shows a gap between the two. Commits touching more than 50 files add `touched` edges but no `co_changed` ones, so a mass reformat does not couple everything to everything.

`imports` edges are the dependencies between files, read from each file's contents at every commit that changed it: an edge opens at the commit that introduced the import and closes at the one that dropped it, so `bib rank --rel imports --directed --valid-at 2024` is the most depended-on files as of 2024. Imports are found by pattern, not by parsing, in Python, JavaScript/TypeScript, Rust and C/C++, and resolved against the files that existed at that commit; anything that is not a file in the repository (standard library, packages) is left out. A renamed file keeps its node, so its dependencies carry over. `--no-imports` skips reading contents.

The view has five lenses, each answering a different question; the number keys and `tab` switch between them, and `--lens` picks the first:

1. **Structure**: the directory tree, with what imports what drawn across it. Every directory holds its files on a small sphere around itself and its sub-directories on the side facing away from its parent, each given room for its whole subtree, so the shape radiates from the root, nothing overlaps, and nothing ever moves.
2. **Map**: the same relations as a force layout, where clusters find their own place.
3. **Dependencies**: files alone, held together only by `imports`.
4. **Coupling**: files that changed in the same commit at least three times, however far apart they live in the tree.
5. **People**: who has touched which files.

All of them draw directories as a quiet grey skeleton and files as the coloured content: the six largest top-level directories get a hue each, the rest share one grey, `imports` edges are light so dependencies read apart from the tree, and a file is sized by how many files import it, which is also what earns it a label. `--rel` makes a lens of your own from any relations (`--layout force` for a simulation even when `contains` is drawn) and `--color-by ext` colours by file type instead.

The graph lives in memory unless you `--save` it; saved, it is an ordinary bibliotheke graph with a data model, so `bib rank --rel co_changed`, `bib path`, `bib communities` and `bib export` all work on it. The scanner is a separate package, `bibliotheke.repo`, that only uses the public API.

## Graph view

`bib view` opens a 3D force-directed graph drawn with [plotui](https://pypi.org/project/plotui/). Drag to rotate, scroll to zoom, hover a node to light up its neighbourhood, click a node or edge to inspect it. It takes the same time filters as the read commands. It needs a terminal with the Kitty graphics protocol (Ghostty, Kitty, iTerm2 3.5+, WezTerm).

The default `map` theme draws a network map on a near-black canvas. Nodes are sized by how connected they are, edges are thin translucent strokes in their endpoints' colours, everything is antialiased, and the best-connected nodes are labelled. `--theme classic` gives the original look. A graph can set its own defaults in `bibliotheke.yaml`:

```yaml
schema_version: 1
view:
  theme: map
  color_by: type        # or a fact key, or an edge (nodes take their target's colour)
  labels: 16            # name this many of the best-connected nodes
  colors:               # per category, #rrggbb
    paper: "#f0f0f4"
    concept: "#66d9a6"
```

Click a legend row to hide that category, click again to bring it back.

`bib view --tree contains` (or `tree: contains` under `view:` in `bibliotheke.yaml`) lays any graph with a hierarchy out as a still tree instead of a force simulation; see the repository view below for what that looks like.

When the graph has dated records, a time bar runs under the plot. Drag it, or step with `,` and `.` (`<` and `>` jump), or press `space` to play the whole span: nodes appear and disappear as their facts and edges come into and go out of validity, and edges that are not valid at that time fade into the canvas. The layout is computed once over everything that ever was, so nothing moves while you scrub; a `--valid-at` on the command line sets where the bar starts.

## Globe view

`bib globe` puts the graph on a 3D Earth. A *place* is any node with `lat` and `lon` facts, such as a city. Other nodes are placed by following their edges. `based_in` is checked first, then `located_in`, then `affiliated_with`, so a person sits at their organisation's city unless they have a `based_in` of their own. Each place is a point sized by how many people it holds. Arcs join two places when a shared node, such as a co-authored paper, touches people in both. Click a place to see who is there and what they wrote. It takes the same time filters, and `--who` picks which node types to place (default `person`).

```yaml
globe:
  who: [person]
  theme: dark          # dark, earth, light, blueprint or wire
```

## Example

`examples/people` is a small graph of painters, musicians and AI researchers with approximately dated relations:

```sh
uv run bib view --root examples/people --color-by field
uv run bib view --root examples/people --color-by field --valid-at 1960
uv run python examples/build_people.py    # rebuild it from scratch
```

## Colours

`bib` prints its green, red and yellow as hex, not as the terminal's named colours, so `Data OK` is the same green everywhere rather than whatever a stock Ghostty or iTerm2 maps "green" to. It asks the terminal for its background once and darkens the three colours on a light one; `BIB_BACKGROUND=light` or `dark` overrides that, `COLORFGBG` is the fallback, and `NO_COLOR` turns colour off as usual.

## Development

```sh
uv sync
uv run pytest
```
