# Potential names

Notes from a naming discussion on 2026-10-09. Availability was checked that day:
PyPI by `GET https://pypi.org/pypi/<name>/json` (PyPI treats `git-graf` and
`gitgraf` as the same name), GitHub by repository search on the exact name,
sorted by stars. Re-check before committing to one.

## What we want from the name

- Understandable at a glance, across languages: no name that needs a footnote.
- Says what it feels like (a reference work, a map) rather than the category
  ("knowledge graph" belongs in the tagline).
- Works as a short CLI command. A `git-<name>` binary also makes
  `git <name> ...` work as a git subcommand with no configuration.
- A `git` prefix fits the planned bootstrap hook: run it on any repository and
  get a terminal visualisation (plotui) of it.
- "Git" is a trademark of Software Freedom Conservancy. Their policy allows
  git-prefixed names for tools that work with git, as long as they do not
  suggest official status. Low risk, worth knowing.

## Current name: Bibliotheke

Greek for library. Understandable across Europe, CLI `bib`. Taken on PyPI, but by
us (placeholder 0.1.0 uploaded 2026-09-13). The worry: a library sounds like a
document store, while this is a graph with history.

## Shortlist

| Name | PyPI | GitHub (repos, most-starred) | Notes |
|---|---|---|---|
| **gitgraf** | free | 30, 6★ (unrelated "gitGraffiti") | Graph, Swedish spelling. Front-runner. CLI `graf`, plus `gitgraf` and `git-graf`. |
| **gitkarta** | free | 1, 0★ | *karta* = map. Best fit if the pitch is "map your repo". |
| **gitfakta** | free | 0 | *fakta* = facts. Matches the data model (`add fact`). |
| **gitbok** | free | 11, 0★ | *bok* = book. The library idea in two syllables. |
| **grafbok** | free | 0 | "Graph book". Fallback without the git prefix. |

## Everything else checked

### Swedish / German spellings with the git prefix

| Name | PyPI | GitHub | Notes |
|---|---|---|---|
| gitminne | free | 1, 0★ | *minne* = memory. Opaque outside the Nordics. |
| gitarkiv | free | 0 | *arkiv* = archive. Readable, slightly dusty. |
| gitwissen | free | 0 | German *Wissen* = knowledge. |
| gitlexikon | free | quiet | lexicon |
| gitsaga | free | 11, 0★ | "saga" is a web-framework term. |
| gitspar | free | quiet | *spår* = trace. Opaque. |
| gitvet | free | quiet | *vet* = know. Opaque. |
| gitbuch | free | not checked | German *Buch* = book. |
| gitnat | free | not checked | *nät* = net, without the ä. |

### Graf without git

| Name | PyPI | GitHub | Notes |
|---|---|---|---|
| grafen | free | 196, mostly graphene projects | Swedish for "the graph" and for graphene (a carbon lattice). Clever, crowded, pronounced unexpectedly by English speakers. |
| grafkit | free | 4, 1★ | Generic "toolkit". |
| grafnet | free | not checked | |
| grafik / gitgrafik | free | not checked | *grafik* = graphics. Misleading. |
| timegraf | free | 1, 0★ | Most honest description of the tool; not pretty. |
| factgraf | free | not checked | |
| knowngraf | free | not checked | |
| grafit | taken | | Swedish for graphite. |
| grafo | taken | | |
| graphgit | taken | | |

### Time and knowledge words with the git prefix

| Name | PyPI | GitHub | Notes |
|---|---|---|---|
| gitknown | free | 6, 1★ | Pairs with `--known-at`. |
| gitwhen | free | 3, 2★ | Time travel in one word. Playful; may age badly. |
| gitknit | free | 4, 1★ | Facts knitted together. A bit twee. |
| gittruth | free | 3, 0★ | Grandiose. |
| gitsince / gitthen / gitknew | free | ~0 | Read as fragments. |
| gitchrono / gitlineage / gitrecall / gitmemory / githistory / gitannals / gitpast / gittime | free | gitmemory 13 repos, 19★; others not checked | |

### Other git-prefixed names

| Name | PyPI | GitHub | Notes |
|---|---|---|---|
| gitnodes | free | 9, 4★ | Literal, dull. |
| gitkg | free | 3, 0★ | Short; needs explaining. |
| gitsphere | free | 16, 0★ | Matches the globe view. Generic. |
| gitorbit | free | 14, 18★ | |
| gitweave / gitbrain / gitwise / gitcanon / gitlattice / gitthink / gitsight / gitworld / knowgit | free | gitworld 247 repos; gitwise 64; gitbrain 55; others quiet | |
| gitatlas | free | 27, 47★ | Atlas = book of maps. Rejected: didn't like it. |
| gitcosmos | free | 3, 0★ | Rejected: didn't like it. |
| gitnotes | free | 709, 262★ | Rejected: crowded, and `git notes` is a built-in git command. |
| gitgraph | free | gitgraph.js 3,070★, gitgraph.nvim 449★ | Rejected: owned in practice by gitgraph.js. |
| gitnexus | free | GitNexus 47,797★ | Rejected. |
| gitlens | free | VS Code extension | Rejected. |
| gitstore, gitlore, gitmind, gitmap, gitmesh, gitledger, gitlinks, gitchart, gitgalaxy, gitverse, gitview, gitvis, gitscope, gitwiki, gitweb, gitbook, gitology, gittrail, gitnet, gitnote, graphit | taken | | |

### Plain words (nearly all taken on PyPI)

| Name | PyPI | Notes |
|---|---|---|
| factbook | free | Rejected: sounds like Facebook. |
| factfile, gazetteer, knowledgegraph, cyclopedia, yearbook, lorebook, dossiers | free | Weak or need explaining. |
| annales, chronik, tabularium, epoche | free | Rejected: need explaining (Annales was the first suggestion). |
| bibliothek, bibliotheca, biblioteca, biblio, libris, librarium, clio, kleio, annals, chronicle, chronos, kronos, kairos, tempus, tempora, mnemosyne, historia, codex, palimpsest, scriptorium, archivum, atlas, almanac, almanack, compendium, lexicon, ledger, register, memex, timeline, chronology, encyclopedia, dossier, lore, logbook, casebook, worldbook, constellation, tapestry, nexus, lattice, mindmap, notebook, saga, graf | taken | |
