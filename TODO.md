# TODO

Written 14 Sep 2026, the day the portfolio went live at mattaguilar.com.
The portfolio carries its own fork of `frontend/` (copied at `62aa4eb`, plus
an Architecture tab that only exists there); anything that changes the demo
here has to be re-exported and copied across. `PLAN.md` is the history.

## 1 · Make sure it actually works

- **UI action bus.** Drive `/analytics/chat` end to end on the Pi through the
  real bridge (not the demo's scripted replies): every action type the bus
  knows, from a real prompt, and confirm the UI actually moves. Write down
  which ones don't.
- **Books ↔ music in the chat.** The agent should draw good connections
  between the reading log and the listening history. Test it with real
  prompts; keep the transcript of a good one — it becomes the demo (see 3).

## 2 · Data

- Upload the latest Goodreads CSV export (the `goodreads.py` pipeline is
  manual on purpose).
- Import the latest Ultimate Guitar "My Tabs" page through `ug.py`.
- After both: rerun `scripts/export_demo_fixtures.py` so the demo's reading
  log and guitar data catch up.

## 3 · The demo

- **Revamp the pre-recorded chat** around the music/book taste link that was
  so striking months ago. Today's scripted replies are placeholders. Script it
  from a real transcript (1), then re-export.
- The Mood / Energy panel is empty in the demo: the mood pipeline's output is
  not in the fixture export yet. Add it.
- Check the demo's reading log shows the refreshed Goodreads data after (2).

## 4 · Port from the portfolio's copy

Three changes were made in the portfolio's fork and are worth having here.
Diffs are small and were checked against this repo's HEAD on 14 Sep 2026.

- `panels/DriftAnalysis.jsx`: compare each genre's **share** (`pct`) of
  listening, era vs now, in percentage points — not raw play counts. An era
  is a year and "now" is thirty days, so subtracting plays made every genre
  fall ~95% whatever actually changed. Also drops deltas under 0.5 pts and
  shows "was% → now%".
- `TimeMachine.jsx` and `panels/EraBookmarks.jsx`, `fmt()`: add
  `timeZone: 'UTC'`. The range strings are plain calendar dates, so
  `'2024-01-01'` rendered as Dec 2023 anywhere behind Greenwich.
- `store/uiStore.js`: `timeMachineCompareMode` defaults to `'vs_now'` in the
  fork so the Time Machine shows a comparison on first load. Optional.

## 5 · Docs

- Re-check `README.md` against `crontab -l` and the code: the portfolio's
  architecture page found the schedule 90 minutes off and the dbt /
  LangGraph sections describing a layer that is no longer live. `c9361fb`
  may have fixed some of this; verify rather than assume.
