# Tastemaker

**Live demo:** https://matta628.github.io/tastemaker/ — a static snapshot
of ~3 years of my real listening data. Chat/action-bus responses are
pre-recorded in this build (see [Demo Mode](#demo-mode)); self-hosted on my
Pi, they call Claude live.

A personal taste graph and AI agent built on real behavioral data. Tastemaker ingests years of listening history, reading history, and guitar practice logs, pipelines everything into a columnar database, and puts an AI agent on top that reasons across all of it — surfacing cross-domain connections between music, books, and guitar, generating opinionated playlists, and powering a four-page analytics suite controllable entirely through natural language.

Self-hosted on a Raspberry Pi 5. No cloud services except Claude itself, called through the local `claude` CLI on the subscription — not the metered API.

---

## Demo Mode

The link above is a static build hosted on GitHub Pages — it has no backend at all. Two things make that work without it being mistaken for the live app:

- **Real data, canned decisions.** The dashboard, Discover, Time Machine, and Deep Dive pages are all rendering ~3 years of my actual listening history, exported at build time. The one thing that's simulated is Claude's decision-making: chat replies and AI Action Bus responses are pre-scripted conversations, not a live model call. Everything downstream of that — navigation, chart filters, era comparisons — is the same real code the live app uses, just driven by a scripted `ui_actions` payload instead of a live one.
- **Self-hosted, it's live.** Running on my own Raspberry Pi, both chat surfaces call Claude for real, per-message, against the same database. The demo banner at the top of the page always says which mode you're in.

## What It Does

### Personal AI Agent

A conversational agent that knows you specifically — not music in general. It queries your actual listening history before making any recommendation. It cannot give generic suggestions because it has to run SQL against your data first.

**What the agent can do:**

- **Cross-domain connections** — "What books would pair well with the music I've been listening to this winter?" The agent joins your scrobble history with your Goodreads library through a shared `taste_tags` table that links artists and books in the same genre space. It can identify that you've been deep in post-rock lately and that correlates with the kind of literary fiction you've rated 5 stars.

- **Personalized playlist generation** — Ask for a playlist in plain English. "Songs for a rainy Sunday." "Late-night focus music." "New artists I haven't heard in the jazz-adjacent space." The agent queries your behavioral data first, then calls the Last.fm API for discovery, anti-joins against your full scrobble history at the artist level (not just the track level, so it won't surface Thom Yorke solo tracks as "discovery" when you've clearly heard him via Radiohead), and sends the final playlist to Apple Music via an iOS Shortcuts bridge — one tap on your phone.

- **Guitar recommendations grounded in listening** — The agent checks what you're currently learning (status, difficulty, your own notes like "struggling with F chord"), cross-references your recent scrobbles, and suggests songs to learn next that match both your current technical level and what you're actually listening to.

- **Behavioral context tags** — Unlike generic mood tags sourced from the internet, Tastemaker computes personal behavioral tags from your own scrobble timestamps. If a track shows up in your history consistently after midnight, it gets tagged `late_night` with a confidence score based on the fraction of plays in that window. Same for seasons. "Songs I actually listen to in winter" and "songs the internet says sound wintry" are different things.

- **ML mood analysis** — Lyrics for ~60-70% of the library are fetched from Genius, then run through a zero-shot NLP pipeline that produces 14 multi-label mood tags (melancholic, euphoric, anxious, tender, defiant, nostalgic, dark, hopeful, lonely, romantic, bitter, raw, peaceful, restless). The agent can query these alongside behavioral data: *"late night sad songs"* → join `track_mood` WHERE `melancholic = ANY(tags)` AND `track_context_tags.tag = 'late_night'` AND `confidence >= 0.5`.

- **Persistent memory** — Conversation threads are checkpointed to SQLite and survive server restarts. You can pick up a conversation where you left off.

**Agent architecture:** LangGraph `create_react_agent` with a proper tool loop and state management. Five tools: `query_database` (read-only DuckDB SQL), `build_playlist` (Apple Music bridge), `track_similar_lookup` (Last.fm), `artist_top_tracks` (Last.fm), and `discover_tracks` (genre-based discovery with full scrobble anti-join). Streamed token-by-token over SSE. Traced end-to-end in LangSmith.

---

### Analytics Dashboard

Four data-dense pages. All charts are Highcharts. All state is Zustand. Every page has the AI chat panel in the sidebar.

#### Dashboard

Eight charts showing the full picture of your listening history, all filterable by time range (7 days through all-time):

- **Activity over time** — Line chart of total plays, with auto-adjusting granularity (daily for short ranges, weekly/monthly for longer ones)
- **Genre breakdown** — Pie chart from `artist_tags`, clickable to filter all other charts to a single genre (the genre cross-filter propagates to the top entities bar chart)
- **Mood / Energy** — Pie chart from `track_mood` ML output
- **Top Artists / Albums / Tracks** — Switchable bar chart; respects the genre cross-filter
- **Listening heatmap** — Hour of day × day of week grid, showing when you actually listen
- **Plays by day of week** — Bar chart showing weekday listening patterns
- **New artists discovered** — Line chart of first-time artist appearances over time
- **Streak calendar** — GitHub-style contribution graph of daily listening activity

Charts are draggable — you can reorder the layout. Each chart has a hide button. The AI action bus can toggle chart visibility, reorder them, and highlight specific charts in response to natural language.

#### Deep Dive

Search any artist, album, or track and get a dedicated view with:

- **Time series** — Play count over time at daily/weekly/monthly/yearly granularity, switchable between line/area/bar
- **Compare mode** — Overlay any other entity on the same chart for direct visual comparison
- **Stats panel** — Pre-computed stats: all-time plays, plays across 7d/30d/90d/180d/1y/2y/5y windows with period-over-period deltas, rank (all-time and recent), first/last heard, listening streak, peak week
- **Breakdown panel** — For artists: album-by-album breakdown. Drill down to album or track Deep Dives.
- **Similar panel** — Last.fm similarity graph. Click any similar artist to navigate directly.
- **Annotations** — Toggle markers on the time series for notable events

#### Discover

A fully configurable data table for slicing and dicing the pre-aggregated stats. Switch between Artists, Albums, and Tracks. Three view modes: table, cards, or split.

**Columns (Artist view, grouped by category):**
- Identity: name, genre
- Volume: all-time plays, unique tracks, unique albums
- Recency: days since last heard, last heard date, first heard date
- Trend: plays over 7d / 30d / 90d / 180d / 1y / 2y / 5y, plus period-over-period delta columns for each
- Streak: longest streak (days), current streak
- Rank: all-time rank, 90-day rank, rank delta

Albums and tracks have the same shape.

**Filters:** Any column, any operator (eq, neq, gt, gte, lt, lte, contains, in_last_days). Stacked filters with AND logic. The filter builder UI is exposed to the AI action bus — "show me artists I haven't listened to in over 60 days" builds and applies the filter directly.

**Visualizations:** Attach a chart to the filtered table — bar, scatter, bubble, or pie. Configure axes freely (x, y, and bubble size are all independent column choices).

**Artist Sets:** Create named groups (e.g., "Current Favorites", "Guilty Pleasures"), add/remove members, and use them as a scope filter on any Discover query.

**Saved Reports:** Any combination of entity type + columns + filters + sort + visualization can be saved as a named report and reloaded in one action.

#### Time Machine

Go back to any year in your listening history. Pick a preset year (2019–2025) or set a custom date range. The page renders the full Dashboard chart suite for that era — activity, genre breakdown, top artists, heatmap, streak calendar.

**Compare mode:** Side-by-side view of two eras. Three modes: off, vs Now (your selected era alongside current), vs Era (two arbitrary eras side by side). The visual diff immediately shows how your taste has shifted — which genres grew, which artists fell off, how your weekly listening patterns changed.

**Drift analysis panel** — Quantifies taste shift between two periods: which artists rose or fell the most, genre composition change over time.

---

### AI Action Bus

Every analytics page has a chat panel that controls the UI with natural language. This is not a chatbot that describes what you should do — it directly executes UI changes.

**How it works:**
1. Every chat request includes a `context_snapshot`: current page, active entity, open panels, active time range, active metric, Discover state (columns, filters, sort), compare entities, Time Machine state, and the list of `available_actions` valid for the current page
2. The backend injects this snapshot into a system prompt alongside the full action specification (29 actions with typed parameters)
3. Claude returns structured JSON: `{ "response": "...", "ui_actions": [{ "type": "...", "payload": {...} }] }`
4. The `useActionBus` hook executes the action array in sequence, with guard checks against the `available_actions` list
5. Zustand state updates → components re-render normally
6. The UI shows a per-action status log (done / skipped / error)

**Full action vocabulary (29 actions):**

| Category | Actions |
|---|---|
| Navigation | `navigate`, `global_search`, `show_toast` |
| Dashboard | `set_time_range`, `set_metric`, `set_top_n`, `toggle_chart` |
| Deep Dive | `set_granularity`, `set_chart_type`, `toggle_annotations`, `add_compare_entity`, `remove_compare_entity`, `drill_down`, `open_panel`, `close_panel` |
| Discover | `set_entity_type`, `set_view`, `set_columns`, `apply_filter`, `clear_filters`, `set_sort`, `set_viz_type`, `set_viz_axes`, `load_report` |
| Discover (User API) | `save_report`, `delete_report`, `create_set`, `add_to_set`, `remove_from_set`, `apply_set_filter`, `clear_set_filter` |
| Time Machine | `set_era`, `set_era_preset`, `toggle_compare_mode`, `set_compare_era` |

Examples of what this enables in practice:
- *"Compare my listening in 2021 vs 2023"* → `set_era` + `toggle_compare_mode: vs_era` + `set_compare_era`
- *"Show me artists I've ignored for over 2 months, sorted by how much I used to play them"* → `set_entity_type: artists` + `apply_filter: days_since_last_heard > 60` + `set_sort: plays_1y desc`
- *"Scatter plot: total plays vs streak length, bubble size = 30d trend"* → `set_viz_type: bubble` + `set_viz_axes`
- *"Go to Radiohead's Deep Dive and open the stats panel"* → `navigate` + `open_panel: stats`

---

## Data Sources & Enrichment Pipeline

### What Gets Ingested

| Source | What | How | Schedule |
|---|---|---|---|
| Last.fm | Every scrobble since 2019 (track, artist, album, timestamp) | Incremental sync via watermark | Nightly at 3am |
| Last.fm | Artist tags (genre/mood, weight 0–100) | `artist.getTopTags` per artist | After each sync |
| Last.fm | Artist similarity graph (top 10 similar artists per artist) | `artist.getSimilar` | After each sync |
| Last.fm | Community top tracks per artist | `artist.getTopTracks` (on-demand via agent) | On-demand |
| Goodreads | Full library (title, author, rating, shelf, date read) | CSV export + OpenLibrary enrichment | Weekly |
| OpenLibrary | Book subjects, genres, descriptions | HTTP enrichment with local JSON cache | With Goodreads sync |
| MusicBrainz | Artist country, formed year, artist type (Group vs Person), tags | HTTP enrichment | One-time + manual |
| Genius / lyrics.ovh | Full lyrics text | HTTP with disk cache | On-demand pipeline |
| Guitar app | Songs being learned, difficulty 1–5, status, free-text notes, practice timestamps | Direct React → FastAPI writes | Real-time |

### What Gets Computed

| Table | What | How |
|---|---|---|
| `track_mood` | 14 multi-label mood tags per track, confidence scores | Zero-shot NLP on lyrics via `analyze_mood.py` |
| `track_context_tags` | Personal behavioral tags: time-of-day, season, frequency | Computed from scrobble timestamp distributions |
| `taste_tags` | Cross-domain junction linking artists and books in shared genre space | dbt mart model |
| `listening_sessions` | 30-minute session windows from raw scrobbles | dbt mart model |
| `artist_stats` / `album_stats` / `track_stats` | Pre-aggregated play counts across 9 time windows, ranks, deltas, streaks | Full truncate + recompute after each sync (~3–5 seconds) |

---

## Tech Stack

| Layer | Tech |
|---|---|
| Backend API | FastAPI (Python), async |
| Database | DuckDB — single file, columnar engine, 10–100× faster than row stores for analytical aggregates |
| Data transforms | dbt — SQL models with built-in tests (not_null, unique, referential integrity) |
| AI agent | Headless `claude` CLI (Claude Sonnet) driven by a host-side bridge, domain tools over MCP |
| Frontend | React + Vite + Tailwind CSS |
| Charts | Highcharts (line, bar, pie, heatmap, scatter, bubble, calendar heatmap) |
| State | Zustand |
| Routing | React Router v6 |
| Hosting | Raspberry Pi 5 2GB + Docker Compose |
| Remote access | Tailscale (WireGuard mesh — no public exposure) |
| Web serving | Nginx |
| Apple Music bridge | iOS Shortcuts + `shortcuts://` URL scheme |

---

## Architecture Decisions Worth Noting

**DuckDB over Postgres** — For a single-user analytical workload, DuckDB's columnar engine is dramatically faster for the GROUP BY-heavy queries that power the analytics pages. The stats tables pre-aggregate the most expensive computations so Discover queries are instant. Zero ops overhead — it's a file.

**Pre-aggregated stats tables** — `artist_stats`, `album_stats`, and `track_stats` are fully rebuilt after every Last.fm sync (full truncate + recompute in ~3–5 seconds). This means Discover filters and sorts run against pre-computed columns with no GROUP BY at query time. The tradeoff is that period columns are relative to rebuild time, not query time.

**dbt for transforms** — Raw data is never modified. Every clean table is a reproducible SQL model with tests. Adding a new enrichment table is a new `.sql` file with `{{ ref() }}` dependencies.

**LangGraph over raw API calls** — The agent tool loop, state management, and retry logic would have been hundreds of lines hand-rolled. LangGraph handles it. The checkpointer gives persistent conversation threads for free.

**Behavioral tags over community tags** — `track_context_tags` is computed from your own scrobble timestamps — not from what the internet thinks a track sounds like. "Late night music" means tracks you actually listen to after midnight, with statistical confidence. This is a fundamentally different signal than Last.fm's community tags.

**Artist-level anti-join for discovery** — The `discover_tracks` tool filters against every artist ever scrobbled, not just tracks. This is the only way to reliably exclude side projects and solo work of known artists from discovery results.

**Raspberry Pi + Tailscale** — Always-on, ~$80 one-time vs. $20–40/month for equivalent cloud compute. The full stack runs in Docker Compose. Tailscale gives a stable IP reachable from anywhere on your phone without exposing anything publicly.

---

## Database Schema (Overview)

Six layers from raw ingest to user-saved configurations:

1. **Raw** — Verbatim ingest: `raw_scrobbles`, `raw_books`, `pipeline_state` (watermark)
2. **Guitar** — Direct app writes: `guitar_songs`, `practice_log`
3. **Cleaned dimensions** — dbt staging + marts: `artists`, `albums`, `tracks`, `books`, `scrobbles`, `listening_sessions`, `taste_tags`
4. **Enrichment** — `artist_tags`, `artist_similar`, `track_tags`, `track_mood`, `artist_mb`, `track_lyrics`, `track_context_tags`
5. **Analytics stats** — `artist_stats`, `album_stats`, `track_stats` (pre-aggregated, 9 time windows each)
6. **User API persistence** — `user_dashboards`, `dashboard_charts`, `explore_layouts`, `user_reports`, `user_sets`, `set_members`

---

## API Surface (Selected)

**Guitar / Core:** Full CRUD on guitar songs, practice log timestamps, Last.fm sync trigger, SSE streaming agent chat, Apple Music playlist generation.

**Analytics data (16+ endpoints):** Activity over time, top albums, genre breakdown, mood breakdown, listening heatmap, day-of-week distribution, new artist discovery rate, streak calendar, plus Deep Dive endpoints for per-entity time series / stats / albums / similar artists across artist, album, and track entity types.

**Discover:** `GET /analytics/entities/{artists|albums|tracks}` — queries the pre-aggregated stats tables with arbitrary column filters, sort, and pagination. Filter operators validated against a `_VALID_OPS` allowlist; sort columns against `_VALID_SORT`. All values parameterized.

**AI action bus:** `POST /analytics/chat` — accepts `{ prompt, context_snapshot }`, returns `{ response, ui_actions[] }`.

**User API:** Named dashboard configs, saved Discover reports, artist/track sets, Deep Dive layout preferences — all persisted in DuckDB and synced to Zustand on load.

---

## Deployment

```
Raspberry Pi 5 2GB
├── Docker Compose (restart: always)
│   ├── FastAPI backend          (port 8000)
│   ├── React frontend via Nginx (port 3000)
│   ├── DuckDB                   (volume-mounted — survives rebuilds)
│   └── cron                     (pipeline scheduler)
├── claude-bridge.service        (host-side, port 8787 on the Docker gateway)
│   └── spawns `claude -p` per turn → MCP tool server → same DuckDB file
└── Tailscale daemon → reachable from iPhone anywhere
```

### Claude bridge

The backend makes **no direct Anthropic API calls**. Every Claude-powered
feature — the guitar chat, playlist generation, and the AI Action Bus — posts to
`scripts/claude_bridge.py`, a small host-side service that shells out to the
`claude` CLI.

The reason is billing: the CLI runs under the subscription OAuth token in
`~/.claude_token`, so these calls cost nothing per-request, where the old
`ANTHROPIC_API_KEY` path was metered (and eventually hit a zero balance, which
is what killed chat entirely until this landed). The token can't live in the
backend image — it's deliberately slim and has no `claude` binary — so the CLI
runs on the host and the container reaches it over the Docker bridge gateway,
authenticated with a shared `BRIDGE_TOKEN`.

The agent's five domain tools (`query_database`, `build_playlist`, …) reach
Claude through an MCP stdio server (`backend/agent/mcp_server.py`) that
re-exports the exact tool objects in `backend/agent/tools.py`, so tool schemas
live in one place. Streaming is preserved end to end — the CLI's
`--include-partial-messages` gives real token deltas, which the bridge
normalizes and the backend re-emits as the same SSE events the frontend already
consumed. Multi-turn memory uses the CLI's own `--resume` sessions, which
replaced the LangGraph SQLite checkpointer.

Setup:
```bash
claude setup-token                      # writes CLAUDE_CODE_OAUTH_TOKEN
echo 'CLAUDE_CODE_OAUTH_TOKEN=...' > ~/.claude_token && chmod 600 ~/.claude_token
echo "BRIDGE_TOKEN=$(openssl rand -base64 24)" > ~/.claude_bridge_env && chmod 600 ~/.claude_bridge_env
# mirror that same BRIDGE_TOKEN into .env so the container can authenticate

.venv/bin/pip install -r requirements-bridge.txt
cp scripts/claude-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now claude-bridge
sudo loginctl enable-linger mambo       # so it starts at boot, not just at login
```

Health check: `curl localhost:8000/agent/bridge-health`
Logs: `journalctl --user -u claude-bridge -f`

### Automated pipelines

Cron drives everything through `scripts/pipelines.sh`, which POSTs to the
backend's own pipeline endpoints rather than running `python -m
backend.pipelines.*` directly. That's deliberate: DuckDB allows one writer, and
the API container already holds the database, so the pipeline runs as a child
of that same process instead of fighting it for the lock.

```
0 1 * * *   pipelines.sh lyrics       # lyrics.ovh, incremental
30 1 * * *  pipelines.sh mood         # Claude mood tagging (needs lyrics first)
0 2 * * *   pipelines.sh lastfm       # incremental, watermark on scrobbled_at
0 5 * * 0   pipelines.sh visuals      # Deezer artwork, weekly
0 6 * * 0   pipelines.sh musicbrainz  # artist metadata, weekly
```

Every pipeline is incremental, idempotent, and resumable — an interrupted run
(reboot, rate limit, container rebuild) just picks up where it left off on the
next tick. Two `flock`s guard them: one per pipeline so a slow pass can't stack
up behind itself, and one global so two different pipelines never overlap.

Schedules deliberately avoid the JobApplicationTracker cron bursts (`:25`/`:35`
on hours 3,7,11,15,19,23) — that project spawns its own `claude` process, and
two at once is more than 2GB of RAM wants to hold.

Stats tables rebuild automatically after every Last.fm sync via `asyncio.create_task`.

Goodreads stays manual: it needs a CSV export uploaded through the UI.

### Mood tagging

`backend/pipelines/analyze_mood_claude.py` scores every lyric against 21 mood
labels through the Claude bridge. It replaces `analyze_mood.py`, which used a
DeBERTa zero-shot classifier and was explicitly laptop-only — torch on this Pi
meant ~1GB of RAM and a 6-18 hour backfill. The Claude version needs no new
dependencies, peaks around 350MB, and costs nothing on the subscription.

Both write the same `track_mood` schema (`tags` plus a `scores` JSON of all 21
labels), so `analyze_mood.py --retag` still works for re-deriving tags at a
different threshold without re-running any model.

### Artwork

`backend/pipelines/fetch_visuals.py` pulls artist and album images from
Deezer's public API (no key) into `data/images/`, indexed by the
`entity_images` table and served at `/images/*`. Roughly 18KB per image, so the
full library is ~130MB.

Deezer was chosen after testing the alternatives: Last.fm's artist images have
been dead for years (`artist.getInfo` returns the same placeholder hash for
everyone), and Cover Art Archive needs a MusicBrainz lookup first and only
covers albums. Candidates are scored rather than taking the first hit —
searching "Nirvana" returns the obscure 1960s UK band ahead of the Seattle one,
so an exact-name match plus fan count breaks the tie.

---

## Project Status

| Phase | Status | Description |
|---|---|---|
| Foundation | Done | DuckDB, Last.fm pipeline, Goodreads + OpenLibrary, dbt models |
| Guitar App | Done | FastAPI CRUD, React PWA, lyrics carousel |
| Pi Deploy | Done | Docker Compose, Tailscale, cron pipelines |
| AI Agent | Done | LangGraph agent, 5 tools, SSE streaming, persistent threads |
| Analytics POC | Done | All 4 pages, AI action bus, 29 actions |
| Apple Music | Next | UI built, iOS Shortcut bridge working; full API pending Apple Developer account |
| Telegram Bot | Planned | `/guitar`, `/read`, `/vibe`, `/playlist` commands wired to the same agent |
