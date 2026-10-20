# Robotics Paper Radar

A desktop app for a weekly robotics reading habit. It pulls new papers from arXiv and ranks them
for your interests, helps you pick three or four to read each week, and has Claude write a
two-minute digest of any paper. It also finds the most-cited papers of any year back to 1998.
Your library and notes stay on your computer.

## Set up on a new computer

You need an internet connection and Chrome or Chromium for the app window (any browser works as a
fallback). For AI digests you also need a Claude Pro or Max plan; everything else works without
one.

**1. Install Claude Code and sign in.** Skip this step if you don't want AI digests.

```bash
curl -fsSL https://claude.ai/install.sh | bash     # macOS, Linux, WSL
claude                                            # sign in with your Claude account, then type /exit
```

On Windows (PowerShell), install it with `irm https://claude.ai/install.ps1 | iex`. Details are in the
[Claude Code quickstart](https://code.claude.com/docs/en/quickstart).

**2. Install uv**, the Python tool that installs the app. Then open a new terminal.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # macOS, Linux
```

On Windows (PowerShell), run `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`.

**3. Get the code and install the app.**

```bash
git clone <repo-url> robotics-paper-radar
uv tool install --editable ./robotics-paper-radar
```

**4. Add it to your app menu and connect it to Claude Code.** The menu entry is Linux only; the
Claude Code connection works everywhere. Skip the second command if you skipped step 1.

```bash
paper-radar install-desktop
paper-radar install-mcp
```

**5. Open Paper Radar** from your app menu, or run `paper-radar`. Then press **Refresh papers**
(about 30 seconds).

**6. Make it yours.** The interests and keywords that ship with the app are the author's. Change
them under **Settings → Interests and Focus keywords**, then press **Save and re-rank**.

**7. Check digests.** **Settings → AI digests** should say *Ready*. Press **Test** to confirm.

## Using it

- **Feed**: new papers ranked for you. Shortlist the promising ones; click a title for its
  abstract, an AI digest, your notes, and why it ranked where it did.
- **This week**: pick three or four papers to read, in order, and mark them as you go.
- **Explore**: any year since 1998. See the most-cited papers, fetch a past month or year, or
  search one.
- **Library**: everything you've collected, searchable by keyword and filterable by year.

From any new Claude Code session you can also say, for example:

- "Using paper-radar, digest this week's papers"
- "Deep-read paper 12 and save your notes"
- "Most cited SLAM papers of 2023"

## If something goes wrong

| Problem | Fix |
|---|---|
| `paper-radar: command not found` | Run `uv tool update-shell`, then open a new terminal. |
| "Claude Code isn't installed" or "isn't logged in" | Do step 1. Run `claude` once in a terminal and sign in, or type `/login`. |
| "Your Claude plan's usage limit is reached" | Wait, or switch **Settings → AI digests → Engine** to an API key. |
| The app window doesn't open | Run `paper-radar open --browser`. |
| "Most cited" says Semantic Scholar is busy | Try again in a minute, or add a free key in **Settings → Citations**. |
| Anything else | `paper-radar paths` shows the server log. |

## Update, move or remove

- **Update:** `git pull` inside the folder; changes apply the next time you open the app. If
  `pyproject.toml` changed, also run `uv tool install --editable --reinstall ./robotics-paper-radar`.
- **Move your library to another computer:** copy `~/.local/share/paper-radar/paper_radar.db` to
  the same place on the new machine.
- **Remove:** run `paper-radar uninstall-mcp`, `paper-radar uninstall-desktop`, then
  `uv tool uninstall robotics-paper-radar`.

## Learn more

[docs/guide.md](docs/guide.md) covers how digests are checked for honesty, the Claude Code
connection, citations, scoring, configuration and the project layout. [spec.md](spec.md) is the
original design. Run the tests with `uv run pytest` inside the folder.
