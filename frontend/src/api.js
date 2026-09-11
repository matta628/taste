const BASE = '/api'

async function request(method, path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status}`)
  if (res.status === 204) return null
  return res.json()
}

function buildQuery(params) {
  const q = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== null && v !== undefined) q.set(k, String(v))
  })
  const s = q.toString()
  return s ? `?${s}` : ''
}

export const api = {
  getSongs: (status) =>
    request('GET', status ? `/songs?status=${status}` : '/songs'),
  addSong: (song) => request('POST', '/songs', song),
  updateSong: (id, updates) => request('PUT', `/songs/${id}`, updates),
  deleteSong: (id) => request('DELETE', `/songs/${id}`),
  logPractice:     (id)    => request('POST',   `/songs/${id}/practice`),
  getPracticeLog:  (id)    => request('GET',    `/songs/${id}/practice`),
  deletePractice:  (logId) => request('DELETE', `/practice/${logId}`),
}

export const analytics = {
  activity:       (p = {}) => request('GET', `/analytics/activity${buildQuery(p)}`),
  topAlbums:      (p = {}) => request('GET', `/analytics/top-albums${buildQuery(p)}`),
  genreBreakdown: (p = {}) => request('GET', `/analytics/genre-breakdown${buildQuery(p)}`),
  moodBreakdown:  (p = {}) => request('GET', `/analytics/mood-breakdown${buildQuery(p)}`),
  heatmap:        (p = {}) => request('GET', `/analytics/heatmap${buildQuery(p)}`),
  dayOfWeek:      (p = {}) => request('GET', `/analytics/day-of-week${buildQuery(p)}`),
  newArtists:     (p = {}) => request('GET', `/analytics/new-artists${buildQuery(p)}`),
  listeningStreak:(p = {}) => request('GET', `/analytics/listening-streak${buildQuery(p)}`),
  topVisuals:     (p = {}) => request('GET', `/analytics/top-visuals${buildQuery(p)}`),

  artistHistory:  (name, p = {}) => request('GET', `/analytics/artist/${encodeURIComponent(name)}/history${buildQuery(p)}`),
  artistStats:    (name)          => request('GET', `/analytics/artist/${encodeURIComponent(name)}/stats`),
  artistAlbums:   (name, p = {}) => request('GET', `/analytics/artist/${encodeURIComponent(name)}/albums${buildQuery(p)}`),
  artistSimilar:  (name)          => request('GET', `/analytics/artist/${encodeURIComponent(name)}/similar`),
  artistTimeline: (name)          => request('GET', `/analytics/artist/${encodeURIComponent(name)}/timeline`),
  artistSessions: (name, p = {}) => request('GET', `/analytics/artist/${encodeURIComponent(name)}/sessions${buildQuery(p)}`),

  albumHistory:   (name, p = {}) => request('GET', `/analytics/album/${encodeURIComponent(name)}/history${buildQuery(p)}`),
  albumStats:     (name, p = {}) => request('GET', `/analytics/album/${encodeURIComponent(name)}/stats${buildQuery(p)}`),
  albumTracks:    (name, p = {}) => request('GET', `/analytics/album/${encodeURIComponent(name)}/tracks${buildQuery(p)}`),

  trackHistory:   (name, p = {}) => request('GET', `/analytics/track/${encodeURIComponent(name)}/history${buildQuery(p)}`),
  trackStats:     (name, p = {}) => request('GET', `/analytics/track/${encodeURIComponent(name)}/stats${buildQuery(p)}`),

  entitiesArtists:(p = {}) => request('GET', `/analytics/entities/artists${buildQuery(p)}`),
  entitiesAlbums: (p = {}) => request('GET', `/analytics/entities/albums${buildQuery(p)}`),
  entitiesTracks: (p = {}) => request('GET', `/analytics/entities/tracks${buildQuery(p)}`),
  setGenreOverride: (body)  => request('PATCH', '/analytics/entities/genre', body),
  setTrackMood:     (body)  => request('PATCH', '/analytics/track/mood', body),
  availableMoods:   ()      => request('GET',   '/analytics/moods'),
  topEntities:    (p = {}) => request('GET', `/analytics/top-entities${buildQuery(p)}`),

  genreTagTracks: (tag, p = {}) => request('GET', `/analytics/genre/${encodeURIComponent(tag)}/tracks${buildQuery(p)}`),
  moodTagTracks:  (tag, p = {}) => request('GET', `/analytics/mood/${encodeURIComponent(tag)}/tracks${buildQuery(p)}`),

  search:         (q, p = {}) => request('GET', `/analytics/search${buildQuery({ q, ...p })}`),
}

export const userApi = {
  getDashboards:       ()           => request('GET',    '/user/dashboards'),
  createDashboard:     (body)       => request('POST',   '/user/dashboards', body),
  updateDashboard:     (id, body)   => request('PATCH',  `/user/dashboards/${id}`, body),
  deleteDashboard:     (id)         => request('DELETE', `/user/dashboards/${id}`),

  getDashboardCharts:  (id)         => request('GET',    `/user/dashboards/${id}/charts`),
  saveChart:           (id, body)   => request('POST',   `/user/dashboards/${id}/charts`, body),
  updateChart:         (id, cid, b) => request('PATCH',  `/user/dashboards/${id}/charts/${cid}`, b),
  deleteChart:         (id, cid)    => request('DELETE', `/user/dashboards/${id}/charts/${cid}`),

  getExploreLayout:    (type)       => request('GET',    `/user/explore-layout/${type}`),
  saveExploreChart:    (type, body) => request('POST',   `/user/explore-layout/${type}/charts`, body),

  getReports:          ()           => request('GET',    '/user/reports'),
  saveReport:          (body)       => request('POST',   '/user/reports', body),
  deleteReport:        (id)         => request('DELETE', `/user/reports/${id}`),

  getSets:             ()           => request('GET',    '/user/sets'),
  createSet:           (body)       => request('POST',   '/user/sets', body),
  deleteSet:           (id)         => request('DELETE', `/user/sets/${id}`),
  getSetMembers:       (id)         => request('GET',    `/user/sets/${id}/members`),
  updateSetMembers:    (id, body)   => request('PATCH',  `/user/sets/${id}/members`, body),

  getMetrics:          (chartType)  => request('GET', `/user/metrics${buildQuery({ chart_type: chartType })}`),
}
