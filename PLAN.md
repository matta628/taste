# Tastemaker — GitHub Pages Demo + Claude-Cost Rework Plan

Written 2026-08-22. This is a plan, not implemented yet. Ordered by priority —
**#1, #5, #2, #4 are what actually make the public link good; #3 is a private
backend/cost change invisible to anyone clicking the link.** If time is tight,
stop after #4.

**Two things in here are easy to conflate but are fully separate efforts —
different code, different goals, no dependency in either direction:**
- **Track A (§3)**: the self-hosted Pi backend stops calling the Anthropic
  API directly and calls headless `claude` instead. Pure backend/cost
  concern. Nobody clicking the GitHub Pages link ever touches this code.
- **Track B (§4)**: the static GitHub Pages demo fakes what Claude-powered
  features *would* do, since that build has no backend at all — live,
  headless, or otherwise. Pure frontend build-time fixtures.

Track B doesn't get "more real" if Track A ships, and Track A isn't a
prerequisite for Track B in either direction — you could do either one
alone, or neither, or both in any order.

---

## 0. Housekeeping (2 min)

- Delete `.env.secret~` (untracked editor backup, contains a live
  `ANTHROPIC_API_KEY` + Last.fm keys in plaintext). Add `*~` to `.gitignore`
  so this can't happen again silently. It was never pushed, but rotate the
  Anthropic key if you want to be safe.

---

## 1. Fix "GitHub Pages shows nothing" — ROOT CAUSE FOUND

**This is not a deploy failure.** The `deploy-demo.yml` workflow works fine —
`gh-pages` branch is populated, last deploy `2026-06-06` matches current
`main` HEAD, `.nojekyll` is present, and `curl` confirms the page and its JS
bundle both return `200` with correct content. GitHub Pages itself is fine.

**Real bug:** `frontend/src/App.jsx:260` renders
`<BrowserRouter>` with **no `basename` prop**. The site is served at
`https://matta628.github.io/tastemaker/`, so the browser pathname is
`/tastemaker/` — but every `<Route>` is declared against root-relative paths
(`"/"`, `"/dashboard"`, `"/explore"`, …). react-router v7 has nothing to
match `/tastemaker/` against `path="/"`, so it renders an empty tree. Blank
page, no console error, no network failure — just a silent router miss.

**Fix:**
```jsx
<BrowserRouter basename={import.meta.env.VITE_BASE_PATH || '/'}>
```
`VITE_BASE_PATH=/tastemaker/` is already set in `frontend/.env.demo` and
already flows into `vite.config.js`'s `base` — just wasn't wired into the
router. One-line fix, unblocks everything else below.

Verify locally with `npm run build:demo && npm run preview -- --base=/tastemaker/`
before pushing, then let the existing workflow redeploy on merge to `main`.

---

## 2. Show ~3 years of real listening data on the demo

**Current state:** `frontend/src/demo/mockFetch.js` only mocks the *guitar
log* endpoints (`/songs`, `/playlists`, `/chats`, `/pipelines/status`,
`/taste/lyrics-snippets`) — leftover from before the analytics dashboard
existed. **Zero `/analytics/*` routes are mocked** (there are ~25 of them —
`activity`, `top-albums`, `genre-breakdown`, `mood-breakdown`, `heatmap`,
`day-of-week`, `new-artists`, `listening-streak`, per-artist/album/track
history+stats+similar+timeline, `entities/*`, `top-entities`, `search`,
`/analytics/chat`, …). Today, once goal #1's routing bug is fixed, visiting
`/dashboard` in the demo build will 404 on every fetch and show broken
charts — so this step is not optional, it's required to make the fixed app
actually show anything meaningful.

**Data reality check:** the copy of `tastemaker.db` sitting in this working
directory is a stale/dev snapshot — `raw_scrobbles` has 122,626 rows spanning
2019-05-01 → 2026-04-25 (real, 7 years), but the derived mart tables
(`artists`, `tracks`, `albums`, `track_mood`, `scrobbles`) are empty *in this
local file*. That's just this checkout being behind, not a real gap — the
live Pi is the system of record and has the full, current, populated
database (it's what the running app actually queries). The export step below
needs to run against **that** DB, not this local one.

**Steps:**
1. Get the export script running against the live data — either `rsync`/`scp`
   the current `tastemaker.db` off the Pi over Tailscale down to wherever
   you run the export, or run the export script directly on the Pi itself
   (simplest: same machine, same DB, no copying).
2. Write a one-off export script (`scripts/export_demo_fixtures.py`) that
   imports the *actual* query functions from `backend/analytics.py` (don't
   reimplement the SQL — reuse it so fixtures match real endpoint shapes)
   and dumps JSON for each `analytics.*` call in `frontend/src/api.js`,
   scoped to a **3-year window** — recommend the most recent 3 years
   (richest mood/tag coverage, most recent enrichment) rather than
   all-time. Cap top-N lists (top 50 artists/albums/tracks is plenty),
   and hand-pick ~8-10 top artists to get full Deep Dive fixtures
   (history/stats/albums/similar/timeline) rather than trying to cover
   every possible `/explore/:type/:id` route.
3. Extend `mockFetch.js`'s `route()` with handlers for every `analytics.*`
   path used in `api.js`, backed by the new fixtures (mirror the existing
   regex-match style already used for `/songs/:id`, `/chats/:id`).
4. Make the demo land on the impressive view: change the root route so
   `VITE_DEMO_MODE=true` redirects `/` → `/dashboard` (or make Dashboard
   the default `NAV` tab in demo builds) — a recruiter shouldn't have to
   click "✦ Analytics →" to find the real content.

---

## 3. Track A — Replace Anthropic API calls with headless `claude` CLI (self-hosted Pi)

> **STATUS: DONE — implemented 2026-08-27.** All four Anthropic call sites are
> gone; the backend no longer imports `anthropic` or `langchain-anthropic` at
> all. See README "Claude bridge" for the shipped architecture, and §7 below
> for what was built and where it diverged from the plan sketched here.


**Scope check: this section is entirely about the self-hosted backend on
your Pi.** It doesn't touch the GitHub Pages demo at all — that build has no
backend, so it can't call the Anthropic API *or* headless `claude` today,
and won't after this either. See §4/Track B for the demo's separate,
frontend-only story. Don't read the two as sequential.

**The real mechanism, verified in `/home/mambo/JobApplicationTracker` (the
"Generate Resume/Cover Letter" button) — not a batch-only pattern, this is a
live UI flow:**

1. `POST /api/jobs/{job_id}/generate-application`
   (`backend/routers/applications.py`) does **not** call Claude. It just
   inserts a `pending` row into an `application_generations` table and
   returns immediately.
2. A **host-side** watcher script, `/home/mambo/scripts/generate_application.py`,
   runs every 2 minutes via cron (`generate_application.sh` wrapper —
   mirrors an existing `rescore.sh`). It polls for `pending` rows and for
   each one shells out:
   ```
   claude -p "/make-application job_descriptions/<job_id>.txt" \
     --allowedTools "Read,Write,Bash" --permission-mode acceptEdits \
     --output-format json
   ```
   (600s timeout), writing the job description to a file first, then
   copying the resulting PDFs back into the tracker's own data dir and
   flipping the DB row to `done` (or `failed`).
3. **Why host-side and not inside the FastAPI container**: the backend's
   Docker image is deliberately slim — no `claude` CLI, no subscription
   OAuth token. That token (`CLAUDE_CODE_OAUTH_TOKEN`, from `claude
   setup-token`, sourced out of `~/.claude_token` on the host) is what
   makes this free — it bills the Claude subscription, not
   `ANTHROPIC_API_KEY`/metered API usage. **This is the actual cost fix**,
   and it only works if the same subscription auth is set up host-side on
   your Pi too — worth checking `claude setup-token` has been run there
   before assuming this saves anything.
4. Rate-limit handling is explicit: a 429/"session limit"/"usage limit"
   failure resets the row to `pending` (not `failed`) so it just retries
   next tick — a subscription cap isn't treated as a real error.
5. Frontend (`JobCard.jsx`) **polls, it doesn't stream** — POST to queue,
   then poll every 5s for `pending → running → done/failed`.

**Current tastemaker call sites (3, one is dead code):**
- `backend/agent/graph.py` — `ChatAnthropic` inside a LangGraph
  `create_react_agent`, 5 tools (`query_database`, `build_playlist`,
  `track_similar_lookup`, `artist_top_tracks`, `discover_tracks`). Powers
  both the guitar-log chat (streamed token-by-token over SSE) and playlist
  generation.
- `backend/main.py` `/analytics/chat` (~line 1327) — inline
  `anthropic.Anthropic(...)` call powering the AI Action Bus. Single-turn,
  strict JSON-in/JSON-out, streamed to the UI as an instant reply.
- `backend/analytics_chat.py` — has its own `build_system_prompt()` and its
  own `Anthropic` import, but is **dead code**: `main.py` only imports
  `AnalyticsChatRequest` from it and reimplements the system prompt and
  client call inline. Collapse this duplication regardless of what else
  happens here.

**Why this pattern doesn't drop in cleanly for all three:** JAT's queue
works because generating a resume is inherently a "click, wait, get a
result later" interaction — a 2-minute cron tick + up to 600s of generation
time is invisible to the UX. Two of tastemaker's three call sites are the
opposite: the action bus chat and the guitar chat agent are typed-message,
expect-an-immediate-reply interactions (one of them literally streams
token-by-token today). Bolting a 2-minute cron-poll queue onto those would
be a large UX regression, not a wash.

**Recommended split:**
1. **Playlist generation** is the one genuine match for the JAT pattern —
   "ask for a playlist, wait a bit, get it" already tolerates latency (it
   already goes through a multi-step tool loop + an iOS Shortcuts bridge).
   Turn it into its own skill (`.claude/skills/build-playlist.md` in *this*
   repo — no need for JAT's two-repo split since the DB/Last.fm tooling
   already lives here), add a `playlist_generations` table + host-side
   watcher script mirroring `generate_application.py`/`.sh` and `rescore.sh`
   exactly (same `~/.claude_token` sourcing, same `flock`, same rate-limit
   → reset-to-pending handling), and switch `PlaylistCreator` from its
   current SSE stream to queue+poll like `JobCard.jsx` does.
2. **Action bus chat and guitar chat**: keep these on a direct SDK call for
   now rather than forcing the queue pattern — but if killing API billing
   matters more than latency, the real (still imperfect) option is a
   *persistent* host-side process (not cron) that stays warm and calls
   `claude -p` per turn under the subscription token, accepting ~1-3s of
   subprocess spawn overhead per message and losing true token streaming
   (SSE would need to switch to polling or chunked-replay-after-the-fact).
   Decide this only after confirming subscription auth actually saves
   money for your usage pattern (point 3 above) — don't do it by default.
3. Either way, collapse `backend/analytics_chat.py`'s dead duplicate system
   prompt into whichever single source of truth ends up calling Claude.

### 3a. Concretized implementation steps (not started)

Fleshed out 2026-08-23, after hitting a live `anthropic.BadRequestError:
Your credit balance is too low` on the Pi's `ANTHROPIC_API_KEY` while trying
to test candidate prompts for §4's demo chat script — direct confirmation
that this section is worth doing, not just theoretically nice. Nothing below
has been implemented yet; this is the concrete checklist for whenever it's
picked up.

**Precondition — verify before writing any code:**
- Confirm `claude setup-token` has been run on the Pi itself (host-side, not
  in a container) and `~/.claude_token` exists there with a
  `CLAUDE_CODE_OAUTH_TOKEN`. If it hasn't, this whole section saves nothing
  until it is — do that first.

**Step 1 — Playlist generation (the JAT-pattern match):**
1. Schema: add a `playlist_generations` table to `backend/db/schema.py`,
   shaped like JAT's `application_generations` —
   `id` (pk), `prompt` (the user's playlist request text), `status`
   (`pending`/`running`/`done`/`failed`), `result` (JSON — the playlist
   payload `build_playlist` currently returns), `error` (text, nullable),
   `created_at`, `updated_at`.
2. Backend: replace the current direct-call playlist endpoint (`backend/main.py`
   ~line 1009, the `agent.astream_events` block for playlists) with:
   - `POST /playlists/generate` — inserts a `pending` row with the prompt,
     returns `{generation_id}` immediately. No Claude call in-process.
   - `GET /playlists/generations/{id}` — returns current row (status +
     result once done), for polling.
3. Skill: add `.claude/skills/build-playlist/SKILL.md` in this repo (no
   need for JAT's two-repo split — the DB/Last.fm tooling already lives
   here). It should read a `playlist_generations` row by id, run the same
   logic currently inline in `backend/agent/graph.py`/`tools.py`
   (`query_database` → reasoning → `build_playlist`), and write the result
   back into the row's `result` column plus flip `status` to `done`.
4. Host-side watcher: `~/scripts/generate_playlist.py` +
   `generate_playlist.sh` wrapper, mirroring
   `~/scripts/generate_application.py`/`.sh` and `rescore.sh` exactly:
   - Cron every 2 min.
   - `flock` to prevent overlapping runs.
   - Polls for `pending` rows in `playlist_generations`.
   - For each: shells out to
     ```
     claude -p "/build-playlist <generation_id>" \
       --allowedTools "Read,Write,Bash" --permission-mode acceptEdits \
       --output-format json
     ```
     with a 600s timeout, sourcing `CLAUDE_CODE_OAUTH_TOKEN` from
     `~/.claude_token` first.
   - On a 429/"session limit"/"usage limit" failure: reset the row back to
     `pending` (not `failed`) so it retries next tick, same as JAT.
5. Frontend: switch `PlaylistCreator` from its current SSE stream to
   queue+poll, mirroring `JobCard.jsx` — `POST /playlists/generate`, then
   poll `GET /playlists/generations/{id}` every 5s until `done`/`failed`.

**Step 2 — Action bus chat + guitar chat (only if step 1 confirms real
savings — do not do this by default, per the plan's original caveat):**
1. Replace the in-process `ChatAnthropic`/`anthropic.Anthropic` calls with a
   **persistent** (not cron-polled) host-side process — cron's 2-minute
   tick is too slow for a typed-message chat UX. This process stays warm
   and, per incoming turn, shells out to
   `claude -p "<message>" --output-format json` under the same
   `CLAUDE_CODE_OAUTH_TOKEN` auth.
2. Expect ~1-3s subprocess spawn overhead per message, and the loss of true
   token-by-token streaming — `chat_stream.js`-style SSE would need to
   become either polling or a chunked replay of the final response after
   the fact (fake-streaming a complete string), not a real stream.
3. Same rate-limit → reset/retry handling as step 1.
4. Collapse `backend/analytics_chat.py`'s dead duplicate system prompt into
   this single call site once it's the one source of truth (per point 3 in
   "Recommended split" above).

**Note:** none of this affects the GitHub Pages demo — that's 100% static
frontend with mocked fetches, no backend involved at all. This step is
purely about your self-hosted Pi instance's cost/architecture.

---

## 4. Track B — Simulate Claude behavior for the static GitHub Pages demo

**Scope check: this section is entirely frontend, build-time fixtures,
shipped inside the static `frontend/dist` bundle.** It has no dependency on
§3/Track A — whether your self-hosted backend ends up calling the Anthropic
SDK (as it does today) or headless `claude` (per Track A) makes zero
difference here, because the GitHub Pages build never calls a backend of
any kind. This is purely about making canned data *look* like the live
feature to someone who only has the static link.

Goal: someone who can't hit your Pi should still understand what the action
bus / chat *would* do live, without it being mistaken for actually live.

**Already exists and can be reused directly:**
- `backend/main.py` has an env-gated `ANALYTICS_CHAT_STUBS` mode — a
  keyword-matching table of ~10 sample prompts → canned `{response,
  ui_actions}` pairs (e.g. "scatter" → scatter-plot Discover state, "top
  artist" → Dashboard 1y filter). This is exactly the fixture data needed
  for `/analytics/chat` in demo mode and isn't touched by `mockFetch.js` at
  all yet — port this table verbatim into `mockFetch.js` as the
  `/analytics/chat` handler (today that path falls through to the
  `console.warn('[demo] unhandled')` 404 fallback, silently breaking the
  action bus in the current demo build).
- `chat_stream.js` and `playlist_stream.js` fixtures already provide canned
  SSE streams for the guitar chat agent and playlist generation — reuse
  as-is, no new work needed there.

**The action bus itself needs zero simulation work — it's already real
code end to end.** Checked `frontend/src/hooks/useActionBus.js`: it's a
generic executor that reads a `ui_actions` array and drives real
`useNavigate()` + real Zustand store setters (`ACTION_CHART_IDS` mapping,
highlight/toast logic, etc.) — it has no idea whether that array came from
a live fetch or `mockFetch.js`. So once §2's real fixtures are wired in and
the scripted `/analytics/chat` turns above return real `ui_actions` payloads
(`navigate`, `set_era_preset`, `toggle_compare_mode`, `apply_filter`, …),
clicking through the scripted conversation will **actually navigate pages,
actually filter/highlight charts, actually flip Time Machine eras** — against
your real exported data, rendered by the real Dashboard/Discover/TimeMachine
components. Only the "decide which actions to fire" step (i.e. Claude) is
canned; everything downstream of that JSON is the live app. This is worth
calling out in the demo-mode banner too — "the UI really is being driven by
these actions, only the model's decision is pre-scripted" is a stronger
claim than "here's a video of it working."

**New work:**
- Add a small, persistent UI badge/banner in demo mode — "Demo mode:
  responses are pre-recorded. Self-hosted, this calls Claude live." — so a
  recruiter doesn't mistake scripted replies for a live model and doesn't
  need to guess.
- The stub table + generic canned SSE fixtures are a floor, not the
  destination — go further and script a **full, real back-and-forth
  conversation** built on the actual curated 3-year dataset from §2, not
  generic placeholder turns. One scripted transcript per chat surface
  (guitar/main chat, action bus), **6-8 user↔agent exchanges each**
  (12-16 messages total), showing off cross-domain reasoning against *your
  real listening history* — e.g.:
  - "What have I been listening to this year vs 2023?" → real numbers,
    real artist names, real genre shift, pulled from your export.
  - A turn that triggers the `query_database` tool against a real question
    ("late night sad songs from this winter") → shows the actual
    tool-call/"thinking" UI with real track names, not a lorem-ipsum
    stand-in.
  - An action-bus turn that chains 3+ real actions (e.g. "compare 2021 vs
    2023" → `set_era_preset` + `toggle_compare_mode` + a real chart
    reacting with real data) so the payoff is visibly grounded.
  - A playlist-generation turn ending in a real (if inert in this build)
    playlist of actual tracks from your library.
  Build each as a scripted turn array (same `{event, data, delayMs}` SSE
  shape `playlist_stream.js`/`chat_stream.js` already use) so the existing
  streaming UI renders replies identically to a live response.

- **Interaction: ghost-text autoplay, not just a passive replay.** Rather
  than the user typing anything real, the *next scripted user message*
  appears as greyed-out placeholder text sitting in the (otherwise empty)
  chat input — the same visual idea as an inline autocomplete suggestion.
  A small hint sits under/beside the input: `Tab to fill in · Enter to
  send`. Behavior:
  - **Tab** — the ghost text is inserted into the real input as editable
    text (purely cosmetic — lets a visitor see it "typed" without having
    typed it themselves).
  - **Enter** (whether or not Tab was pressed first) — sends that scripted
    message exactly as if the visitor had typed and submitted it: it
    appears as a normal outgoing chat bubble, then the corresponding
    scripted agent reply streams in via the existing SSE fixture path.
    Once the reply finishes, the *next* turn's user message becomes the new
    ghost text, and the hint reappears — so a visitor can step through the
    whole 6-8-turn conversation one Enter at a time, at their own pace, or
    hold Enter to blow through it.
  - After the last scripted turn: ghost text/hint disappears, replaced with
    something like *"End of scripted demo — free typing isn't wired up in
    this static build."* Don't let it silently fall through to the generic
    404 fallback in `mockFetch.js`.
  - Build this as one small shared component/hook (e.g. a
    `useGhostScript(turns)` hook feeding both the guitar chat input and the
    action bus chat input) rather than duplicating the Tab/Enter handling
    in each panel.
  - Since this *is* the discovery mechanism, the earlier idea of separate
    clickable "try: ..." prompt chips becomes redundant for these two
    scripted-conversation panels — drop it there. Chips are still worth
    keeping for the standalone stub table (§4's `ANALYTICS_CHAT_STUBS` port)
    if that stays reachable outside the scripted conversation.

---

## 5. Add the live demo link to the README

Once #1 is fixed, add directly under the H1 in `README.md`:

```md
# Tastemaker

**Live demo:** https://matta628.github.io/tastemaker/ — a static snapshot
of ~3 years of my real listening data. Chat/action-bus responses are
pre-recorded in this build (see [Demo Mode](#demo-mode)); self-hosted on my
Pi, they call Claude live.
```

Add a short "Demo Mode" section (anchor referenced above) explaining the
GitHub Pages build vs. the self-hosted Pi build, tying together #1-#4 for a
reader who wants to know what's real vs. simulated.

---

## Suggested order given limited time

Everything that touches the public link (§1, §2, §4/Track B, §5) forms one
sequence. §3/Track A is a separate, independent effort — do it whenever, or
never, without affecting any of the rest:

1. §1 router fix (5 min, unblocks everything)
2. §5 README link (2 min)
3. §2 real data export + analytics mocks (biggest lift, biggest visual payoff)
4. §4/Track B demo "gist" polish (cheap once §2 is done — most of it already
   exists)

Separately, whenever you want it: §3/Track A headless Claude rework
(self-hosted Pi only — has no visible effect on the public link either way).

---

## 6. Session notes — 2026-08-23

**Goodreads library refreshed with a real export.** Ran
`python -m backend.pipelines.goodreads --csv <export>` against a fresh
208-book Goodreads export (transferred from the Chromebook over Tailscale
scp) — `raw_books` in `tastemaker.db` now reflects real, current shelf data
(108 read / 99 to-read / 1 currently-reading).

**Bug found + fixed in `backend/pipelines/goodreads.py`:** `parse_int()`
did `int(s.strip())`, which throws on Goodreads' float-formatted rating
strings (`"5.0"`, `"4.0"`, …) and was silently swallowing the exception,
writing every rated book's rating as `NULL`. 98 of the 208 books had real
ratings that were getting dropped. Fixed to `int(float(s.strip()))` and
re-ran the ingestion (the `ON CONFLICT ... DO UPDATE` backfilled existing
rows correctly). Verified: rating distribution is now
`{0: 110, 5: 50, 4: 33, 3: 13, 2: 2}`, matching the raw CSV. This bug isn't
specific to ratings — worth double-checking `num_pages`/`year_published`
parsing too if Goodreads ever emits those as decimals.

**Local Pi venv note:** system Python is 3.13; `requirements.txt`'s pinned
`pydantic==2.7.4` (and its transitive `pydantic-core`) has no prebuilt wheel
for cp313 and fails to build from source (maturin/pyo3 version mismatch).
Installing the same packages *unpinned* resolves to a cp313-compatible
pydantic and works fine. Not fixed in `requirements.txt` itself — just a
heads-up for the next `pip install -r requirements.txt` on this box.

**Anthropic API credit balance is currently zero** — confirmed via a live
`400 Your credit balance is too low` error when trying to run
`backend/agent/graph.py`'s real agent against a few candidate prompts. This
is the direct trigger for fleshing out §3a above.

**Demo chat opener — still undecided, two real candidates drafted by hand**
(queried `raw_books`/`raw_scrobbles`/`artist_mb` directly instead of calling
the live agent, since the API was down). Task: make the *first* thing shown
in the demo chat (`frontend/src/demo/fixtures/chat_stream.js` +
`chat_messages.json`, `demo-chat-1`) surface a connection between music and
books, rather than burying it as the 4th of 5 sections like today. Two
grounded options, not yet chosen between:

- **"You Finish What You Start — Completely"** — completionism angle:
  every Harry Potter/Percy Jackson/Heroes of Olympus/ACOTAR/Empyrean book
  you've read is rated 5 stars with no dropped series, paired with The
  Strokes at 11,117 plays (~60% ahead of #2, Lana Del Rey at 6,946) — you
  don't sample, you fully colonize a thing.
- **"Your Music and Your Books Are Both Tagged 'Melancholic' — Literally"**
  — genre-tag angle: Elliott Smith is Last.fm-tagged `sadcore`, Lana Del Rey
  `sad girl pop`, Radiohead literally tagged `melancholic` in `artist_mb`;
  cross-referenced against the 5-star literary shelf (*Crime and
  Punishment*, *Steppenwolf*, *Demian*, *Heart of a Dog*, *Jesus' Son*).

Next time: pick one (or ask for a third/combined option), then reorder
`chat_stream.js`'s `CHUNKS` array and `chat_messages.json`'s `demo-chat-1`
assistant message so the chosen section leads, and update `chats.json`'s
title if it should change too.


---

## 7. Session notes — 2026-08-27 (Track A shipped)

**Trigger:** the guitar chat returned "no response" in the UI. Backend logs
showed `400 Your credit balance is too low` on every `/agent/chat` — the same
zero-balance condition §6 recorded on 2026-08-23. Nothing was broken in the
code; the metered API had simply run dry. That made §3 the fix rather than a
someday-item.

**Call sites migrated (4, not 3 — the plan missed one):**
- `backend/agent/graph.py` — `ChatAnthropic` in a LangGraph react agent. **Deleted.**
- `backend/main.py` `/analytics/chat` — inline `anthropic.Anthropic`. **Rewired.**
- `backend/analytics_chat.py` — dead duplicate. **Deleted** (`AnalyticsChatRequest`
  was already redefined locally in `main.py`, so the module was fully unreferenced).
- `backend/analytics.py:13` — `from anthropic import Anthropic`, imported but never
  instantiated. Not in the plan's inventory. **Removed.**

`grep -rn "anthropic" backend/` now returns nothing.

**What was built:**
- `scripts/claude_bridge.py` — persistent host-side FastAPI service on
  `172.18.0.1:8787` (Docker gateway only, never LAN/Tailscale), shared-secret
  auth via `BRIDGE_TOKEN`. Spawns `claude -p` per turn under
  `CLAUDE_CODE_OAUTH_TOKEN`. Concurrency-capped at 2 (2GB Pi, each `claude` is a
  Node process).
- `backend/agent/mcp_server.py` — MCP stdio server re-exporting the same five
  tool objects from `tools.py`, so schemas aren't duplicated per transport.
- `backend/agent/bridge_client.py` — the only module in the container that knows
  the bridge exists.
- `scripts/claude-bridge.service` — systemd **user** unit (needs the user's
  `claude` + token). Requires `loginctl enable-linger` to start at boot.
- `GET /agent/bridge-health` — diagnostic, since every AI feature now depends on
  an external process.

**Where this diverged from the plan above:**
1. **No queue, no cron, no `playlist_generations` table.** §3a specced the JAT
   queue+poll pattern for playlists. It turned out to be unnecessary: `claude -p`
   spawns in ~1-3s, so even playlist generation stays inside a normal request.
   Skipping it also meant **zero frontend changes** — `PlaylistCreator` keeps its
   SSE stream instead of being rewritten to poll.
2. **Streaming was preserved, not lost.** §3a step 2 assumed going headless meant
   giving up token-by-token streaming and fake-replaying a finished string. Wrong:
   `--output-format stream-json --include-partial-messages` emits real
   `text_delta` events. The bridge normalizes them and `main.py` re-emits the
   identical SSE vocabulary the frontend already consumed.
3. **No skill file needed.** §3a step 1.3 wanted `.claude/skills/build-playlist/`.
   MCP tools + `--system-prompt` covered it; the existing `SYSTEM_PROMPT` is
   passed straight through.
4. **The checkpointer was replaced, not ported.** Multi-turn memory is now the
   CLI's own `--resume <session_id>`, with `thread_id → session_id` mapped in
   `data/bridge_sessions.json`. `checkpoints.db` and its compose mount are gone
   (the file is left on disk, unused).

**Gotchas worth remembering:**
- **`--bare` breaks subscription auth.** Its docs say auth becomes "strictly
  `ANTHROPIC_API_KEY` or apiKeyHelper — OAuth and keychain are never read", which
  is exactly backwards from what this needs. Do not add it to trim the prompt.
- **asyncio `StreamReader` caps lines at 64KB.** `stream-json` is one JSON object
  per line, and a `query_database` result table blows past that —
  `ValueError: Separator is found, but chunk is longer than limit`, which
  surfaced only on playlist runs. Fixed with `limit=32MB` on
  `create_subprocess_exec`.
- **MCP wraps returns as `{"result": ...}`.** `build_playlist`'s consumer expects
  its own JSON, so the bridge unwraps that envelope.
- **Tool inputs arrive as `input_json_delta` chunks**, so `content_block_start`
  carries an empty input. Playlist SQL provenance had to read the assembled input
  off the later `assistant` message — hence the separate `tool_input` event.
- The CLI inserts its own `ToolSearch` hop before calling a deferred MCP tool;
  it's filtered out of the UI event stream.

**Also fixed along the way:** `backend/agent/tools.py` documented the Goodreads
reading-status column as `shelf`, which is NULL on all 208 rows (it holds
Goodreads' *custom* shelves, unused here). Real status lives in
`exclusive_shelf` (108 read / 99 to-read / 1 currently-reading). The agent was
querying a column that could only ever return nothing — so book questions would
have failed even with credits. Docstring corrected.

**Not done:** `parse_int` in `goodreads.py` still has the uncommitted
`int(float(...))` fix from §6 — unchanged by this work, still worth committing.


---

## 8. Session notes — 2026-08-27 (automation + visuals)

Follow-on from §7. Goal: make the enrichment pipelines run unattended, add
artist/album artwork, and surface it on the Dashboard.

**Capacity check first (2GB Pi 5, 57G disk):**
- Services are not the RAM story — docker+containerd 105MB, tailscaled 49MB,
  both backends 35MB, bridge 10MB ≈ **200MB total**. `claude` is: a headless
  spawn peaks **231MB** (simple) to ~450MB (tool-heavy). Bridge
  `MAX_CONCURRENT` was therefore dropped 2 → 1.
- Disk was 69% full, but **24GB is reclaimable Docker build cache** living in
  `/var/lib/containerd`, not `/var/lib/docker` (containerd snapshotter) — which
  is why `du /var/lib/docker` misleadingly reported 941M. Plus ~2.8GB `~/.npm`
  and 1.9GB of duplicated Playwright Chromiums. Not yet reclaimed — needs a
  `docker buildx prune -f`.
- Real data is trivial: tastemaker.db 45MB, JAT's job_tracker.db 153MB.

**Mood: rewritten onto Claude, not torch.** `analyze_mood.py` was explicitly
"laptop only (requires transformers + torch)" — on this Pi that meant ~1GB RAM
and an estimated 6-18h backfill (3,175 tracks × 21 labels). New
`analyze_mood_claude.py` batches ~20 tracks per bridge call instead: no new
deps, ~350MB peak, free on the subscription. Same `track_mood` schema, so
`--retag` and `MoodChart` are unchanged. Quality spot-checked and good
("Hit 'Em Up" → angry 0.90 / defiant 0.80; "Voices Carry" → melancholic,
anxious, lonely).

**Visuals: Deezer.** Tested Last.fm (artist images dead — every artist returns
placeholder hash `2a96cbd8b46e...`), Cover Art Archive (albums only, needs a
MusicBrainz lookup, 1 req/s), and Deezer (no key, both entity types, ~18KB
JPEGs). Deezer won. Deezer's *structured* query syntax is worse than plain
search — `artist:"Nirvana"` returns meditation playlists — so it does a plain
search and scores candidates: exact name match first, fan count only as a
tiebreak. Without that, "Nirvana" resolves to a 1960s UK band with 199 fans
over the Seattle one with 10M.

**Three real bugs found and fixed along the way:**

1. **`fetch_lyrics.py` held the DuckDB write lock for its entire run** (~13 min
   over ~1,500 tracks). DuckDB is single-writer, so this blocked every API
   request *and* hard-killed a concurrent visuals backfill with
   `IOException: Could not set lock`. Rewritten to flush in chunks of 50 with
   the connection closed during HTTP. `fetch_visuals.py` was written the same
   way from the start.

2. **Even with short flush windows, the Dashboard still broke** — it fires ~8
   chart requests at once, so one flush took the whole page down together
   (every chart read "No data"). Added `backend/db/connect.py`: a lock-tolerant
   connect with ~2s of retries, wired into `main.db()` and `analytics._db()`.
   Verified 40/40 requests succeed during a live pipeline run, vs. a fully
   blank dashboard before.

3. **`rebuild_stats.py` used `RANK()` where it meant `ROW_NUMBER()`.** The
   `peak_weeks` CTE ranks weeks by play count and joins on `rn = 1`; with
   `RANK()`, every week tied at the peak gets `rn = 1`, so the join fans out one
   stats row per tied week. That inflated `artist_stats` to 2,572 rows for 1,667
   real artists — **905 phantom rows**, e.g. 15 copies of "Los Ángeles Azules".
   Visible as duplicate tiles in the new artwork strip. Fixed in all three
   builders (artist/album/track) with a deterministic `week_start` tiebreak.
   **`rebuild-stats` must be re-run to clear the existing duplicates.**

**Automation that did not previously exist.** The README documented a cron
schedule that was never installed — `crontab -l` had only JobApplicationTracker
entries. Added `scripts/pipelines.sh` (POSTs to the backend's own endpoints, so
pipelines run inside the container that already owns the DB lock) plus real
crontab entries, scheduled off JAT's `:25`/`:35` burst windows on hours
3,7,11,15,19,23. Two flocks: per-pipeline (no self-stacking) and global (no two
pipelines at once) — verified by watching `mood` correctly skip while `visuals`
ran.

**Frontend:** `ArtworkStrip.jsx` above the Dashboard grid — horizontally
scrolling artist/album tiles, artist⇄album toggle, click-through to Deep Dive,
deterministic initials tiles for entities with no artwork (coverage is never
100%), and a count of how many are missing.
