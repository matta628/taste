/**
 * Demo mode fetch interceptor.
 * Installed once before React mounts. Intercepts all /api/* calls and
 * returns fixture data or fake SSE streams — no backend required.
 */

import songs_fixture      from './fixtures/songs.json'
import playlists_fixture  from './fixtures/playlists.json'
import chats_fixture      from './fixtures/chats.json'
import chat_messages_fix  from './fixtures/chat_messages.json'
import pipeline_fixture   from './fixtures/pipeline_status.json'
import lyrics_fixture     from './fixtures/lyrics.json'
import playlist_stream    from './fixtures/playlist_stream.js'
import chat_stream        from './fixtures/chat_stream.js'
import guitar_chat_script from './fixtures/guitar_chat_script.js'
import action_bus_script  from './fixtures/action_bus_script.js'
import analytics_fixture  from './fixtures/analytics.json'
import { canonicalKey, parsePathAndQuery } from './analyticsKey.js'

// ---------------------------------------------------------------------------
// In-memory state (resets on page refresh — intentional for demo)
// ---------------------------------------------------------------------------

let songs     = songs_fixture.map(s => ({ ...s }))
let playlists = playlists_fixture.map(p => ({ ...p }))
let chats     = chats_fixture.map(c => ({ ...c }))
let practice  = {}  // { song_id: [{ log_id, song_id, practiced_at }] }

function uuid() {
  return 'demo-' + Math.random().toString(36).slice(2, 10)
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function noContent() {
  return new Response(null, { status: 204 })
}

// ---------------------------------------------------------------------------
// SSE stream helper
// ---------------------------------------------------------------------------

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms))
}

function makeSSEStream(events) {
  return new Response(
    new ReadableStream({
      async start(controller) {
        const enc = new TextEncoder()
        for (const { event, data, delayMs } of events) {
          await sleep(delayMs)
          const line =
            event === 'message'
              ? `data: ${data}\n\n`
              : `event: ${event}\ndata: ${data}\n\n`
          controller.enqueue(enc.encode(line))
        }
        controller.close()
      },
    }),
    { headers: { 'Content-Type': 'text/event-stream' } }
  )
}

// ---------------------------------------------------------------------------
// Analytics — served from a pre-exported fixture lookup keyed by canonical
// "path?query" strings (see scripts/export_demo_fixtures.py and
// ./analyticsKey.js). Anything not in the export (a param combo outside the
// ~10 hand-picked Deep Dive artists, an arbitrary custom date range, ...)
// degrades gracefully to an empty result rather than a 404 — a real backend
// would answer, this demo build just doesn't have that slice baked in.
// ---------------------------------------------------------------------------

function decodePathSegments(path) {
  return path.split('/').map(decodeURIComponent).join('/')
}

function lookupAnalyticsFixture(rawPath) {
  const { path, params } = parsePathAndQuery(decodePathSegments(rawPath))
  return analytics_fixture[canonicalKey(path, params)]
}

function entitiesRows(entityType) {
  return analytics_fixture[`/analytics/entities/${entityType}`]?.rows || []
}

function queryEntities(rows, params) {
  const { sort_by = 'rank_all_time', sort_dir = 'asc', limit = 100, offset = 0, search, genre_filter } = params
  let filtered = rows
  if (search) {
    const s = search.toLowerCase()
    filtered = filtered.filter((r) =>
      Object.values(r).some((v) => typeof v === 'string' && v.toLowerCase().includes(s))
    )
  }
  if (genre_filter) {
    const g = genre_filter.toLowerCase()
    filtered = filtered.filter((r) => (r.genre || '').toLowerCase() === g)
  }
  const dir = sort_dir === 'desc' ? -1 : 1
  const sorted = [...filtered].sort((a, b) => {
    const av = a[sort_by]
    const bv = b[sort_by]
    if (av == null && bv == null) return 0
    if (av == null) return 1
    if (bv == null) return -1
    if (av < bv) return -1 * dir
    if (av > bv) return 1 * dir
    return 0
  })
  const off = Number(offset) || 0
  const lim = Number(limit) || 100
  return { total: sorted.length, rows: sorted.slice(off, off + lim) }
}

function statsFromEntities(entityType, name, artist) {
  const nameField = { artist: 'artist', album: 'album', track: 'track' }[entityType]
  const rows = entitiesRows(`${entityType}s`)
  return rows.find((r) =>
    r[nameField]?.toLowerCase() === name.toLowerCase() &&
    (!artist || entityType === 'artist' || r.artist?.toLowerCase() === artist.toLowerCase())
  )
}

function demoSearch(q, limit) {
  if (!q || q.length < 2) return []
  const s = q.toLowerCase()
  const results = [
    ...entitiesRows('artists')
      .filter((a) => a.artist.toLowerCase().includes(s))
      .map((a) => ({ type: 'artist', name: a.artist, secondary: null, plays: a.total_plays })),
    ...entitiesRows('albums')
      .filter((a) => a.album.toLowerCase().includes(s) || a.artist.toLowerCase().includes(s))
      .map((a) => ({ type: 'album', name: a.album, secondary: a.artist, plays: a.total_plays })),
    ...entitiesRows('tracks')
      .filter((t) => t.track.toLowerCase().includes(s) || t.artist.toLowerCase().includes(s))
      .map((t) => ({ type: 'track', name: t.track, secondary: t.artist, plays: t.total_plays })),
  ]
  results.sort((a, b) => (b.plays || 0) - (a.plays || 0))
  return results.slice(0, Number(limit) || 20)
}

function handleAnalytics(rawPath) {
  const { path, params } = parsePathAndQuery(decodePathSegments(rawPath))

  if (path === '/analytics/entities/artists') return json(queryEntities(entitiesRows('artists'), params))
  if (path === '/analytics/entities/albums') return json(queryEntities(entitiesRows('albums'), params))
  if (path === '/analytics/entities/tracks') return json(queryEntities(entitiesRows('tracks'), params))

  if (path === '/analytics/search') return json(demoSearch(params.q, params.limit))

  // Entities the export gave a full Deep Dive (everything reachable from the
  // Dashboard and Time Machine lists) have their own stats fixture; anything
  // else in the Explore tables is answered from its entity row below.
  const exported = lookupAnalyticsFixture(rawPath)
  if (exported !== undefined) return json(exported)

  let m
  if ((m = path.match(/^\/analytics\/artist\/([^/]+)\/stats$/))) {
    const row = statsFromEntities('artist', m[1])
    return row ? json(row) : json({ detail: 'Artist not found in stats' }, 404)
  }
  if ((m = path.match(/^\/analytics\/album\/([^/]+)\/stats$/))) {
    const row = statsFromEntities('album', m[1], params.artist)
    return row ? json(row) : json({ detail: 'Album not found in stats' }, 404)
  }
  if ((m = path.match(/^\/analytics\/track\/([^/]+)\/stats$/))) {
    const row = statsFromEntities('track', m[1], params.artist)
    return row ? json(row) : json({ detail: 'Track not found in stats' }, 404)
  }

  console.warn('[demo] no fixture for analytics path, returning empty:', rawPath)
  return json([])
}

// ---------------------------------------------------------------------------
// AI Action Bus — ported verbatim from backend/main.py's ANALYTICS_CHAT_STUBS
// dev-stub table (keyword match -> canned {response, ui_actions}). Live,
// self-hosted, this same table backs real Claude calls in dev; here it's
// the only version that will ever run, since this build has no backend.
// ---------------------------------------------------------------------------

const CHAT_STUBS = [
  [['summer', '2021'], {
    response: '[STUB] Navigating to Time Machine and setting era to Summer 2021 (June–August).',
    ui_actions: [
      { type: 'navigate', payload: { path: '/timemachine' } },
      { type: 'set_era_preset', payload: { preset: '2021', from: '2021-06-01', to: '2021-08-31' } },
      { type: 'highlight_chart', payload: { chart_id: 'timemachine_chart' } },
      { type: 'show_toast', payload: { message: 'Time Machine → Summer 2021' } },
    ],
  }],
  [['radiohead', 'year'], {
    response: '[STUB] Opening Radiohead deep dive with all-time yearly granularity.',
    ui_actions: [
      { type: 'navigate', payload: { path: '/explore/artist/Radiohead' } },
      { type: 'set_time_range', payload: { period: 'all' } },
      { type: 'highlight_chart', payload: { chart_id: 'deepdive_chart' } },
      { type: 'show_toast', payload: { message: 'Deep Dive → Radiohead (all time)' } },
    ],
  }],
  [['compare', 'lana', 'mitski'], {
    response: '[STUB] Opening Lana Del Rey deep dive, switching to all-time, and comparing with Mitski.',
    ui_actions: [
      { type: 'navigate', payload: { path: '/explore/artist/Lana Del Rey' } },
      { type: 'set_time_range', payload: { period: 'all' } },
      { type: 'open_panel', payload: { panel: 'Compare' } },
      { type: 'add_compare_entity', payload: { type: 'artist', id: 'Mitski' } },
      { type: 'highlight_chart', payload: { chart_id: 'deepdive_chart' } },
      { type: 'show_toast', payload: { message: 'Compare: Lana Del Rey vs Mitski' } },
    ],
  }],
  [["haven't", 'month'], {
    response: '[STUB] Filtering Discover to artists with 50+ total plays but not heard in 6+ months.',
    ui_actions: [
      { type: 'navigate', payload: { path: '/discover' } },
      { type: 'set_entity_type', payload: { entity_type: 'artist' } },
      { type: 'apply_filter', payload: { sort_by: 'total_plays', sort_dir: 'desc' } },
      { type: 'highlight_chart', payload: { chart_id: 'discover_table' } },
      { type: 'show_toast', payload: { message: 'Discover → dormant artists filter' } },
    ],
  }],
  [['scatter'], {
    response: '[STUB] Scatter plot of top 50 artists by total plays vs days since last heard.',
    ui_actions: [
      { type: 'navigate', payload: { path: '/discover' } },
      { type: 'set_entity_type', payload: { entity_type: 'artist' } },
      { type: 'set_top_n', payload: { n: 50 } },
      { type: 'set_viz_type', payload: { viz_type: 'scatter' } },
      { type: 'set_viz_axes', payload: { x_metric: 'total_plays', y_metric: 'days_since_last_heard' } },
      { type: 'highlight_chart', payload: { chart_id: 'discover_viz' } },
      { type: 'show_toast', payload: { message: 'Discover → scatter: plays vs recency' } },
    ],
  }],
  [['top artist'], {
    response: '[STUB] Showing top artists over the last year on the Dashboard.',
    ui_actions: [
      { type: 'navigate', payload: { path: '/dashboard' } },
      { type: 'set_time_range', payload: { period: '1y' } },
      { type: 'highlight_chart', payload: { chart_id: 'top_entities' } },
      { type: 'show_toast', payload: { message: 'Dashboard → top artists (1y)' } },
    ],
  }],
  [['genre'], {
    response: '[STUB] Opening the Dashboard to show your genre breakdown chart.',
    ui_actions: [
      { type: 'navigate', payload: { path: '/dashboard' } },
      { type: 'highlight_chart', payload: { chart_id: 'genre_mood' } },
      { type: 'show_toast', payload: { message: 'Dashboard → genre breakdown' } },
    ],
  }],
  [['deep dive', 'nirvana'], {
    response: '[STUB] Opening Nirvana deep dive.',
    ui_actions: [
      { type: 'navigate', payload: { path: '/explore/artist/Nirvana' } },
      { type: 'highlight_chart', payload: { chart_id: 'deepdive_chart' } },
      { type: 'show_toast', payload: { message: 'Deep Dive → Nirvana' } },
    ],
  }],
  [['compare', 'year'], {
    response: '[STUB] Navigating to Time Machine and enabling compare mode for this year vs last year.',
    ui_actions: [
      { type: 'navigate', payload: { path: '/timemachine' } },
      { type: 'set_era_preset', payload: { preset: '2024', from: '2024-01-01', to: '2024-12-31' } },
      { type: 'highlight_chart', payload: { chart_id: 'timemachine_chart' } },
      { type: 'show_toast', payload: { message: 'Time Machine → 2024 vs 2025 compare' } },
    ],
  }],
  [['discover'], {
    response: '[STUB] Opening the Discover page with all artists sorted by total plays.',
    ui_actions: [
      { type: 'navigate', payload: { path: '/discover' } },
      { type: 'set_entity_type', payload: { entity_type: 'artist' } },
      { type: 'apply_filter', payload: { sort_by: 'total_plays', sort_dir: 'desc' } },
      { type: 'highlight_chart', payload: { chart_id: 'discover_table' } },
      { type: 'show_toast', payload: { message: 'Discover → all artists' } },
    ],
  }],
]

function matchChatStub(prompt) {
  const p = (prompt || '').toLowerCase()
  for (const [keywords, response] of CHAT_STUBS) {
    if (keywords.every((k) => p.includes(k))) return response
  }
  return {
    response: `Demo mode: no scripted response matches "${prompt}". Try one of the suggested prompts — self-hosted, this calls Claude live for any question.`,
    ui_actions: [],
  }
}

// ---------------------------------------------------------------------------
// Ghost-script conversations — useGhostScript (see hooks/useGhostScript.js)
// walks the visitor through a fixed sequence of real, pre-authored turns per
// chat surface. Matched by exact prompt text so either surface stays correct
// no matter what order turns are replayed in (new chat, page refresh, ...).
// Anything that doesn't match a scripted turn falls back to the existing
// single-shot fixtures (chat_stream / CHAT_STUBS) below.
// ---------------------------------------------------------------------------

function matchGuitarScript(message) {
  return guitar_chat_script.find((t) => t.user === message)?.stream ?? null
}

function matchActionBusScript(prompt) {
  const turn = action_bus_script.find((t) => t.user === prompt)
  return turn ? { response: turn.response, ui_actions: turn.ui_actions } : null
}

// ---------------------------------------------------------------------------
// Route handlers
// ---------------------------------------------------------------------------

async function route(url, method, body) {
  const path = url.replace(/^\/api/, '')

  // Health
  if (path === '/health' && method === 'GET')
    return json({ status: 'ok' })

  // Songs
  if (path === '/songs' && method === 'GET')
    return json(songs)

  if (path === '/songs' && method === 'POST') {
    const song = { ...body, song_id: uuid(), created_at: new Date().toISOString(), updated_at: new Date().toISOString() }
    songs.unshift(song)
    return json(song, 201)
  }

  const songMatch = path.match(/^\/songs\/([^/]+)$/)
  if (songMatch) {
    const id = songMatch[1]
    if (method === 'PUT') {
      songs = songs.map(s => s.song_id === id ? { ...s, ...body, updated_at: new Date().toISOString() } : s)
      return json(songs.find(s => s.song_id === id))
    }
    if (method === 'DELETE') {
      songs = songs.filter(s => s.song_id !== id)
      return noContent()
    }
  }

  const practiceMatch = path.match(/^\/songs\/([^/]+)\/practice$/)
  if (practiceMatch) {
    const id = practiceMatch[1]
    if (method === 'GET')
      return json(practice[id] || [])
    if (method === 'POST') {
      const entry = { log_id: uuid(), song_id: id, practiced_at: new Date().toISOString() }
      practice[id] = [...(practice[id] || []), entry]
      return json(entry, 201)
    }
  }

  const practiceDeleteMatch = path.match(/^\/practice\/([^/]+)$/)
  if (practiceDeleteMatch && method === 'DELETE') {
    const lid = practiceDeleteMatch[1]
    for (const id in practice) practice[id] = practice[id].filter(e => e.log_id !== lid)
    return noContent()
  }

  // Chats
  if (path === '/chats' && method === 'GET')
    return json(chats)

  if (path === '/chats' && method === 'POST') {
    const chat = { chat_id: uuid(), title: body.title, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }
    chats.unshift(chat)
    return json(chat, 201)
  }

  const chatMatch = path.match(/^\/chats\/([^/]+)$/)
  if (chatMatch) {
    const id = chatMatch[1]
    if (method === 'DELETE') {
      chats = chats.filter(c => c.chat_id !== id)
      return noContent()
    }
  }

  const chatMsgsMatch = path.match(/^\/chats\/([^/]+)\/messages$/)
  if (chatMsgsMatch) {
    const id = chatMsgsMatch[1]
    if (method === 'GET')
      return json(chat_messages_fix[id] || [])
    if (method === 'POST')
      return json({ ok: true }, 201)
  }

  // Playlists
  if (path === '/playlists' && method === 'GET')
    return json(playlists)

  const plMatch = path.match(/^\/playlists\/([^/]+)$/)
  if (plMatch && method === 'DELETE') {
    playlists = playlists.filter(p => p.playlist_id !== plMatch[1])
    return noContent()
  }

  // Agent — playlist (SSE stream)
  if (path === '/agent/playlist' && method === 'POST')
    return makeSSEStream(playlist_stream)

  // Agent — chat (SSE stream)
  if (path === '/agent/chat' && method === 'POST')
    return makeSSEStream(matchGuitarScript(body?.message) ?? chat_stream)

  // Pipelines — status
  if (path === '/pipelines/status' && method === 'GET')
    return json(pipeline_fixture)

  // Pipelines — any trigger/upload (silently succeed)
  if (path.startsWith('/pipelines/') && method === 'POST')
    return json({ status: 'demo_mode' })

  // Taste / lyrics
  if ((path === '/taste/lyrics-snippets' || path.startsWith('/taste/top-tracks')) && method === 'GET')
    return json(lyrics_fixture)

  // Analytics
  if (path.startsWith('/analytics/') && method === 'GET')
    return handleAnalytics(path)

  if (path === '/analytics/chat' && method === 'POST')
    return json(matchActionBusScript(body?.prompt) ?? matchChatStub(body?.prompt))

  if ((path === '/analytics/entities/genre' || path === '/analytics/track/mood') && method === 'PATCH')
    return json({ ok: true }) // demo mode: edits aren't persisted

  // Fallback
  console.warn('[demo] unhandled:', method, path)
  return json({ error: 'not found' }, 404)
}

// ---------------------------------------------------------------------------
// Fetch interceptor
// ---------------------------------------------------------------------------

export function installMockFetch() {
  const original = window.fetch
  window.fetch = async (input, init = {}) => {
    const url    = typeof input === 'string' ? input : input.url
    const method = (init.method || 'GET').toUpperCase()

    if (!url.startsWith('/api')) return original(input, init)

    let body = null
    if (init.body) {
      try {
        body = JSON.parse(init.body)
      } catch {
        body = init.body
      }
    }

    return route(url, method, body)
  }
}
