# Paper Radar guide

The full reference: how each page works, digests and their honesty checks, the Claude Code connection, citations, scoring, configuration and project layout. For setting the app up, see the [README](../README.md).

## The weekly loop

1. **Feed.** Press **Refresh papers** to pull the last 14 days from arXiv (about 1,100 papers,
   about 30 seconds).
   - Each entry shows its score, why it matched, topic and tags, any venue the authors stated,
     citations when known, and the AI takeaway once written.
   - **Shortlist** or **Skip** papers (skips can be undone). **Summarize the first 10 abstracts**
     screens the top of the list in one go.
2. **Paper details** (click a title). Four tabs:
   - **AI digest**: the digest, plus buttons to write it from the abstract or the full paper;
   - **Abstract**: the authors' own text;
   - **Why it ranks here**: the score, factor by factor;
   - **Notes**: your notes, which save on their own.
3. **This week.** Choose three or four papers, put them in order, and mark them *Reading* and
   *Read*. **Prepare full-text digests** reads each one's PDF ahead of time. Last week's
   unfinished papers carry over in one click.
4. **Explore.** Pick any year back to 1998, when arXiv's robotics category began:
   - **Most cited** lists that year's top robotics papers by citations, optionally narrowed by a
     topic such as "SLAM", and adds them to your library in a click.
   - **Everything on arXiv** fetches a whole past month or year and ranks it for your interests
     without the recency factor. Its search box finds keywords among that period's papers in your
     library. **Search arXiv for "…"** asks arXiv directly and adds the best 200 matches, so you
     don't have to fetch the whole year.
5. **Library.** Every paper collected so far, searchable including your notes, and filterable by
   year.
6. **Settings.** Interest weights, focus keywords, refresh window, papers per week, the scoring
   formula, the digest engine and model, the Claude Code connection, and an optional Semantic
   Scholar key.

The theme follows your system's light or dark setting. Change it under **⋮ → Settings → Theme**.

## AI digests

Choose how Claude writes digests under **Settings → AI digests → Engine**.

**Claude Code (default).** The app runs your installed Claude Code (`claude -p`) on your Claude
subscription.
- There's no API key and no per-use bill. Each digest counts toward your plan's usage limits, like
  any Claude Code request.
- It runs with no tools, no MCP servers and no skills, in an empty folder, and sees only the paper
  text the app passes in.
- Any `ANTHROPIC_API_KEY` is hidden from it, so your plan is what's used.
- Make sure `claude` works in a terminal first: run it once and sign in with `/login`.

**Anthropic API key.** You pay per use. Create a key at console.anthropic.com and paste it into
Settings. It's saved to `~/.config/paper-radar/.env`, readable only by you, never stored in the
database and never shown back.

A digest follows spec.md §7: one-line takeaway, problem, core idea, method steps, evidence
(setting, robot or dataset, baseline, result, where it appears), what's new, limitations, why it
may matter to you, a reading checklist, what isn't reported, and a self-rated confidence.

**Every digest is labelled with what it was based on:**

- *Abstract only*: the title, metadata and abstract.
- *Full text*: the arXiv PDF, downloaded on request, with page markers kept so evidence can cite
  "p. 6, Table II". The bibliography is dropped.
- *Part of the full text*: long papers are cut at a page boundary once the length limit is reached
  (120,000 characters by default, adjustable). The digest says which pages it read.

**Honesty checks.** Before a digest is saved, it must pass these checks:

- It matches the JSON schema, with categories and tags restricted to the taxonomy.
- An abstract-only digest doesn't cite figures, tables, sections or pages it never saw.
- Page citations fall within the pages that were read.
- Every number in the summary and evidence appears in the inspected text, so an invented "94%
  success" is caught.

If a check fails, the digest gets one repair round with the problems spelled out. Anything still
wrong is shown above it. The prompt treats the paper as untrusted data, keeps the authors' claims
apart from interpretation, and says "Not reported in the inspected text" instead of guessing.

**Caching and cost.** Digests are cached per paper version, prompt version, model and source
level, so reopening one is free, and a new arXiv version marks a digest "out of date". Every Claude
call is logged; this month's totals appear in Settings. Estimated costs with an API key:

| Model (Settings) | Price per million tokens (in / out) | Typical digest |
|---|---|---|
| Claude Opus 5.5 (default) | $4 / $20 | ~$0.07 abstract, ~$0.22 full text |
| Claude Sonnet 5.5 | $2 / $10 | about half of Opus |
| Claude Haiku 5.5 | $0.10 / $0.50 | well under a cent |

**Effort** (default *medium*, Anthropic's recommended starting point for Opus 5.5) sets how hard
the model thinks; higher levels take longer and use more of your plan or budget.

**Fallback.** With an API key on Opus and Sonnet, the request opts into Anthropic's server-side
fallback: if a safety filter wrongly declines a paper, it's retried on a substitute model.

## Your Claude Code sessions (MCP)

`paper-radar install-mcp` registers Paper Radar as an MCP server in Claude Code for all your
projects. So does **Settings → AI digests → Connect to Claude Code**. In any new Claude Code
session you can then ask, for example:

- "Using paper-radar, digest this week's papers"
- "Deep-read paper 12 with the deepread skill and save your notes"
- "What were the most cited SLAM papers of 2023? Add the top five to my library"

The server's tools:

| Tool | What it does |
|---|---|
| `find_papers`, `get_paper` | Search the library and read a paper's record. |
| `this_week` | This week's reading list. |
| `get_full_text` | The PDF's text, with page markers. |
| `digest_instructions` | The same rules and schema the app uses. |
| `save_digest` | Runs the same honesty checks. It refuses a failing digest unless asked to keep it with notes. |
| `add_note`, `set_status` | Write to your notes and reading status. |
| `most_cited` | A year's most-cited papers. |

Digests saved this way appear in the app marked as written "in one of your Claude Code sessions".
A session that was already open when you connected needs restarting.

## Citations and past years

arXiv has no citation data, so the Explore page asks **Semantic Scholar**.
- It merges a paper's arXiv and published versions, so its counts reflect real use. OpenAlex was
  tested too and undercounts recent arXiv papers badly.
- Only papers with an arXiv ID are listed, so each one can be opened and digested.
- It works without a key, but its public pool often answers "busy" (retried automatically). A free
  key from semanticscholar.org/product/api, pasted into **Settings → Citations**, makes it reliable.
- Recent years have had less time to gather citations, so compare papers within a year.

**Past months and years** come straight from arXiv's API, back to November 1998.
- A month of cs.RO is roughly 1,000–2,000 papers, about 20–60 seconds to fetch.
- A whole year runs month by month, taking several minutes. Months finished before you stop are kept.
- A keyword search of arXiv for one period takes a few seconds and searches cs.RO plus your
  neighbouring categories, best matches first.

## How a paper is scored (0–100)

| Factor | Default points | What earns them |
|---|---:|---|
| Topic match | 40 | Your weight for the paper's main topic, relative to your highest weight. A strong secondary topic counts at 70%. |
| Your keywords | 25 | Focus keywords in the title (full credit) or abstract (half), with diminishing returns. |
| Recency | 20 | Halves every 10 days since submission. An unknown date gets half credit. Left out when ranking past years. |
| Code or project | 10 | A code repository (or, for partial credit, a project page) listed by the authors on arXiv. |
| Venue | 5 | Acceptance at a known venue stated by the authors, e.g. "Accepted to CoRL 2026". |

spec.md §6 suggests 45/25/20/10. Five points moved to venue so that accepted papers at strong
venues surface. Every weight can be changed under **Settings → Scoring formula**. Rules the score
follows:

- A venue counts only when the authors state it. "Submitted to" and "under review" never count,
  and workshops are labelled as such.
- A missing code link is never treated as a flaw.
- Citations are shown but don't change the score yet.

## Where things live

| What | Linux location |
|---|---|
| Library (SQLite) | `~/.local/share/paper-radar/paper_radar.db` (a copy goes to `backups/` before any schema migration) |
| Keys and overrides | `~/.config/paper-radar/`: `.env` holds the Claude and Semantic Scholar keys; optional copies of `taxonomy.yaml`, `venues.yaml` or `profile.yaml` override the defaults |
| Server log | `~/.local/share/paper-radar/logs/server.log` |
| Defaults | `src/paper_radar/defaults/` (taxonomy, venues, your CV-based profile) |

To change the taxonomy, venues or default profile, copy the file into `~/.config/paper-radar/`
and edit it, then restart the app. Stored papers are re-classified automatically.
`PAPER_RADAR_DATA_DIR`, `PAPER_RADAR_CONFIG_DIR` and `PAPER_RADAR_DB_PATH` override the locations
(see `.env.example`), and `PAPER_RADAR_CLAUDE_BIN` points to a specific `claude` executable.

Phrase rules in the YAML files:
- Matching ignores case and treats hyphen/space variants and plurals as equal.
- ALL-CAPS phrases (VIO, ACT) match case-sensitively.
- A trailing `*` matches any ending.
- `re:` starts a raw regular expression.
- UK spellings are normalised to US.

The regular refresh runs two arXiv queries: everything in cs.RO, and robotics-flavoured papers
that are only listed in cs.CV, cs.LG, cs.AI or eess.SY. A local filter drops off-topic matches
caused by arXiv's word stemming. Lower `robotics_gate.min_evidence` in `taxonomy.yaml` to be more
inclusive.

## Project layout

```text
src/paper_radar/
  cli.py                       the `paper-radar` command: app window, server, desktop entry, MCP setup
  config.py                    locations (XDG), packaged defaults, environment
  streamlit_app/               the Streamlit script and its theme (.streamlit/config.toml)
  defaults/                    taxonomy.yaml, venues.yaml, profile.yaml
  mcp_server.py                MCP server for your Claude Code sessions
  sources/arxiv_source.py      arXiv Atom API client: throttling, retries, readable errors
  sources/semantic_scholar.py  most-cited papers of a year
  llm/                         provider interface; Claude Code (`claude -p`) and API adapters; key storage
  services/                    ingestion, classification, ranking, signals, deduplication, digests
                               (schema, prompt, checks, caching), full text, explore, reading queue
  repositories/                all SQL: papers, digests and texts, reading queue, settings
  ui/                          pages, paper dialog, digest view, theme
tests/                         pytest suite: fake arXiv, Claude, Claude Code and Semantic Scholar; generated PDFs
```

The UI calls services and repositories only. It contains no SQL, no HTTP and no prompt logic.

## Known limitations

- **Claude Code digests** depend on your plan's usage limits. When a limit is reached, the app
  says so; you can wait or switch to an API key.
- **Most-cited lists** depend on Semantic Scholar's public API, which rate-limits without a key.
- **Full text** relies on the PDF's text layer: scanned PDFs are refused, and equations or tables
  can come out garbled.
- **Desktop entry** is Linux-only. On macOS or Windows, run `paper-radar`.
- **ROS 2**, when sourced, puts another Python's packages on `PYTHONPATH`. The app strips it for
  its server and for Claude Code, and `pyproject.toml` disables ROS's pytest plugins.

## Next steps

- **Phase 3, continued.** Hugging Face Daily Papers upvotes for attention on brand-new papers, and
  an optional citation factor in the score for older papers.
- **Phase 4.** Weekly review statistics, BibTeX/Zotero export, and author watchlists.
