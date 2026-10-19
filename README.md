# Robotics Paper Radar

A small desktop app for a weekly reading habit. It pulls recent robotics papers from arXiv,
sorts them into a robotics taxonomy and ranks them against your interests, with a score you can
read line by line. It helps you pick three or four papers to read each week and, if you add a
Claude API key, writes a two-minute screening digest from a paper's abstract or its full text.
Notes and reading history stay on your machine.

Phases 1 and 2 of [spec.md](spec.md) are built. Phase 1 covers discovery, ranking and tracking;
Phase 2 adds AI digests. Everything except digests works without any API key.

## Install

You need [uv](https://docs.astral.sh/uv/) and an internet connection. uv fetches Python 3.12 if
needed.

```bash
uv tool install --editable /path/to/robotics-paper-radar   # puts `paper-radar` on your PATH
paper-radar install-desktop                                # adds Paper Radar to the app menu (Linux)
```

Then open **Paper Radar** from your app menu, or run `paper-radar`.

The app opens in its own window: a Chrome, Chromium, Edge or Brave app window with a private
profile, so there are no tabs and no address bar, and it gets its own taskbar entry. Closing the
window stops the app. Without one of those browsers it opens in your default browser.
`--editable` means edits to this folder take effect the next time you open the app; leave it out
for a fixed snapshot. Remove everything with `paper-radar uninstall-desktop` and
`uv tool uninstall robotics-paper-radar`.

Other ways to run it:

| Command | What it does |
|---|---|
| `paper-radar` | Open the app window (same as `paper-radar open`). |
| `paper-radar open --browser` | Open it in your default browser instead. |
| `paper-radar serve [--open]` | Run the local web server in this terminal (Ctrl+C stops it). |
| `paper-radar paths` | Show where the library, settings, key file and logs live. |
| `uv run paper-radar` | Run from this folder without installing. |

Run the tests with `uv run pytest`.

## The weekly loop

1. **Feed**: press **Refresh papers** to pull the last 14 days from arXiv (about 1,100 papers in
   about 30 seconds; arXiv asks for a three-second pause between requests). Each entry shows its
   score, why it matched, its topic and tags, any venue the authors stated, and, once written,
   the paper's AI takeaway. **Shortlist** what looks promising and **Skip** the rest (skips can
   be undone). **Summarize the first 10 abstracts** screens the top of the list in one go.
2. **Paper details** (click a title). The dialog has four tabs:
   - **AI digest**: the digest, plus buttons to write it from the abstract or the full paper.
   - **Abstract**: the authors' own text.
   - **Why it ranks here**: the score broken down factor by factor.
   - **Notes**: your notes, which save when you click outside the box.
3. **This week**: choose three or four papers from your shortlist, order them, and mark them
   *Reading* and *Read*. **Prepare full-text digests** reads each chosen paper's PDF ahead of
   time. Last week's unfinished papers carry over in one click.
4. **Library**: every paper collected so far, searchable including your notes.
5. **Settings**: interest weights, focus keywords, refresh window, papers per week, the scoring
   formula, and the AI digest setup.

The theme follows your system's light or dark setting. Change it under **⋮ → Settings → Theme**.

## AI digests (Phase 2)

Add a key under **Settings → AI digests**. Create one at
[console.anthropic.com](https://console.anthropic.com); a Claude.ai subscription doesn't cover API
use. The key is saved to `~/.config/paper-radar/.env`, readable only by you. It is never stored in
the paper database and never shown back. An `ANTHROPIC_API_KEY` environment variable works too.

A digest follows spec.md §7: one-line takeaway, problem, core idea, method steps, evidence
(setting, robot or dataset, baseline, result, where it appears), what's new, limitations, why it
may matter to you, a reading checklist, what isn't reported, and a self-rated confidence.

**What a digest is based on is always labelled:**

- *Abstract only*: the title, metadata and abstract.
- *Full text*: the arXiv PDF, downloaded on request, with page markers kept so evidence can cite
  "p. 6, Table II". The bibliography is dropped.
- *Part of the full text*: long papers are cut at a page boundary once the length limit is
  reached (120,000 characters, about 30k tokens, adjustable). The digest says which pages it read.

**Honesty checks.** Every digest is checked before it is saved:

- It must match the JSON schema, with categories and tags restricted to the taxonomy.
- An abstract-only digest may not cite figures, tables, sections or pages it never saw.
- Page citations must fall within the pages that were read.
- Every number in the summary and evidence must appear in the inspected text. An invented
  "94% success" is caught.

Problems get one repair round with the issues spelled out. Anything still wrong is shown above the
digest as "Some automatic checks didn't pass". The prompt tells Claude to treat the paper as
untrusted data, to separate the authors' claims from its own interpretation, and to write "Not
reported in the inspected text" rather than guess.

**Caching and cost.** Digests are cached per paper version, prompt version, model and source
level, so reopening one is free. A new arXiv version marks the digest "out of date". Each Claude
call is logged with tokens and an estimated cost; this month's total appears in Settings, and the
buttons show a per-digest estimate.

| Model (Settings) | Price per million tokens (in / out) | Typical digest |
|---|---|---|
| Claude Opus 5.5 (default) | $4 / $20 | ~$0.07 abstract, ~$0.22 full text |
| Claude Sonnet 5.5 | $2 / $10 | about half of Opus |
| Claude Haiku 5.5 | $0.10 / $0.50 | well under a cent |

Typical costs are estimates; the app learns your real averages as you use it. Effort (default
*medium*, Anthropic's recommended starting point for Opus 5.5) sets how hard the model thinks;
higher levels cost more. On Opus and Sonnet the request opts into Anthropic's server-side
fallback, so a request that a safety filter wrongly declines is retried on a substitute model.

## How a paper is scored (0–100)

| Factor | Default points | What earns them |
|---|---:|---|
| Topic match | 40 | Your weight for the paper's main topic, relative to your highest weight. A strong secondary topic counts at 70%. |
| Your keywords | 25 | Focus keywords in the title (full credit) or abstract (half), with diminishing returns. |
| Recency | 20 | Halves every 10 days since submission. An unknown date gets half credit. |
| Code or project | 10 | A code repository (or, for partial credit, a project page) listed by the authors on arXiv. |
| Venue | 5 | Acceptance at a known venue stated by the authors, e.g. "Accepted to CoRL 2026". |

spec.md §6 suggests 45/25/20/10. Five points moved to venue, so accepted papers at strong venues
surface; this is the only "impact" signal arXiv offers. Every weight can be changed under
**Settings → Scoring formula**. Rules the ranking follows:

- A venue counts only when the authors state it. "Submitted to", "under review" and extensions of
  earlier papers never count, and workshops are labelled as such.
- A missing code link earns no bonus but is never treated as a flaw.
- When two topics score about the same, the topic chip gets a dashed outline.

## Where things live

| What | Linux location |
|---|---|
| Library (SQLite) | `~/.local/share/paper-radar/paper_radar.db` (a copy goes to `backups/` before any schema migration) |
| API key and overrides | `~/.config/paper-radar/` (`.env`, plus optional `taxonomy.yaml`, `venues.yaml` or `profile.yaml` copies) |
| Server log | `~/.local/share/paper-radar/logs/server.log` |
| Defaults | `src/paper_radar/defaults/` (taxonomy, venues, your CV-based profile) |

To change the taxonomy, venues or your default profile, copy the file from
`src/paper_radar/defaults/` into `~/.config/paper-radar/` and edit the copy, then restart the app.
Stored papers are re-classified automatically. `PAPER_RADAR_DATA_DIR`, `PAPER_RADAR_CONFIG_DIR`
and `PAPER_RADAR_DB_PATH` override the locations (see `.env.example`). `paper-radar paths` prints
them all.

Phrase rules in the YAML files:

- Matching ignores case and treats hyphen and space variants and plurals as equal.
- ALL-CAPS phrases (VIO, ACT) match case-sensitively.
- A trailing `*` matches any ending.
- `re:` starts a raw regular expression.
- UK spellings are normalised to US.

### What gets fetched

Each refresh runs two arXiv queries over the lookback window:

- everything in cs.RO;
- robotics-flavoured papers that are only listed in cs.CV, cs.LG, cs.AI or eess.SY.

arXiv's search stems words, so the second query also returns papers about price or face
"manipulation". A local filter keeps only papers with real robotics evidence. To be more
inclusive, lower `robotics_gate.min_evidence` in `taxonomy.yaml`. Full-text digests download
single arXiv PDFs on request, through the same polite rate limiter. PDFs aren't kept; only the
extracted text is.

## Project layout

```text
src/paper_radar/
  cli.py                     the `paper-radar` command: app window, server, desktop entry
  config.py                  locations (XDG), packaged defaults, environment
  streamlit_app/             the Streamlit script and its theme (.streamlit/config.toml)
  defaults/                  taxonomy.yaml, venues.yaml, profile.yaml
  sources/arxiv_source.py    arXiv Atom API client: throttling, retries, readable errors
  llm/                       provider interface, Claude adapter, API key storage
  services/                  ingestion, classification, ranking, signals, deduplication,
                             digests (schema, prompt, checks, caching), full text, reading queue
  repositories/              all SQL: papers, digests and texts, reading queue, settings
  ui/                        pages, paper dialog, digest view, theme
tests/                       pytest suite with fake arXiv, fake Claude client and generated PDFs
```

The UI calls services and repositories only. It contains no SQL, no HTTP and no prompt logic.

## Known limitations

- **Impact signals.** The only one is a venue the authors state. Citation counts and community
  attention (Semantic Scholar, OpenAlex, Hugging Face upvotes) are Phase 3.
- **Digests.** They need your own Claude API key and cost a few cents each. They were developed
  against a scripted model and the live API's error responses. Judge the first real digests
  critically and tell me where the prompt should change.
- **Full text.** It relies on the PDF's text layer: scanned PDFs are refused, and equations or
  tables can come out garbled, which the digest's confidence should reflect.
- **Refresh.** Every refresh re-fetches the whole lookback window (8–9 requests, about 30 seconds).
- **Desktop.** The app-menu entry is Linux-only. On macOS and Windows, run `paper-radar`.
- **ROS 2.** Sourcing it puts another Python's packages on `PYTHONPATH`. The app strips it for its
  server, and `pyproject.toml` disables ROS's pytest plugins.

## Next steps

- **Phase 3: influence signals.** Hugging Face Daily Papers upvotes give community attention for
  brand-new AI-robotics papers. OpenAlex or Semantic Scholar citations would power a "landmark
  papers in my topics" lane. Both are linked to digests and the feed's ranking.
- **Phase 4.** Weekly review statistics, export to BibTeX or Zotero, and author watchlists.
