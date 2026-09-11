import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { HighchartsReact } from 'highcharts-react-official'
import Highcharts from 'highcharts'
import { AnalyticsShell } from './AnalyticsShell'
import { ArtistHistoryChart } from './charts/ArtistHistoryChart'
import { ChartCard } from './charts/ChartCard'
import { useChartData } from './charts/useChartData'
import { useUIStore } from '../../store/uiStore'
import { analytics } from '../../api'
import { periodToDates, merge, COLORS } from './charts/chartTheme'

const PERIODS = ['7d', '30d', '90d', '1y', '2y', '3y', '4y', '5y', 'all']
const AUTO_GRAN = { '7d': 'day', '30d': 'day', '90d': 'week', '1y': 'week', '2y': 'month', '3y': 'month', '4y': 'month', '5y': 'month', 'all': 'month' }
const GRAN_OPTIONS = ['day', 'week', 'month', 'year']
const CHART_TYPES = ['line', 'area', 'bar']
const METRIC_OPTIONS = ['plays', 'unique_tracks']

function StatPill({ label, value }) {
  if (value === null || value === undefined) return null
  return (
    <div className="bg-zinc-800/60 rounded-xl px-3 py-2 flex flex-col justify-center">
      <div className="text-[10px] text-zinc-500 uppercase tracking-wide">{label}</div>
      <div className="text-sm font-medium text-zinc-200 mt-0.5">{value}</div>
    </div>
  )
}

function StatsPanel({ name }) {
  const { data, loading } = useChartData(() => analytics.artistStats(name), [name])
  if (loading) return <div className="text-zinc-700 text-xs">Loading stats…</div>
  if (!data) return <div className="text-zinc-700 text-xs">No stats yet — run a sync</div>

  return (
    <div className="grid grid-cols-1 gap-2 flex-1 auto-rows-fr">
      <StatPill label="All-time plays"    value={data.total_plays?.toLocaleString()} />
      <StatPill label="7d plays"          value={data.plays_7d} />
      <StatPill label="30d plays"         value={data.plays_30d} />
      <StatPill label="All-time rank"     value={data.rank_all_time ? `#${data.rank_all_time}` : null} />
      <StatPill label="30d rank"          value={data.rank_30d ? `#${data.rank_30d}` : null} />
      <StatPill label="30d rank Δ"        value={data.rank_30d_delta != null ? (data.rank_30d_delta > 0 ? `▲${data.rank_30d_delta}` : data.rank_30d_delta < 0 ? `▼${Math.abs(data.rank_30d_delta)}` : '—') : null} />
      <StatPill label="Unique tracks"     value={data.unique_tracks} />
      <StatPill label="Unique albums"     value={data.unique_albums} />
      <StatPill label="Longest streak"    value={data.longest_streak_days ? `${data.longest_streak_days}d` : null} />
      <StatPill label="Current streak"    value={data.current_streak_days ? `${data.current_streak_days}d` : '0d'} />
      <StatPill label="Days since last"   value={data.days_since_last_heard != null ? `${Math.round(data.days_since_last_heard)}d ago` : null} />
      <StatPill label="Peak week"         value={data.peak_week_plays ? `${data.peak_week_plays} plays` : null} />
    </div>
  )
}

function AlbumsPanel({ name, fromDate, toDate }) {
  const navigate = useNavigate()
  const { data, loading } = useChartData(
    () => analytics.artistAlbums(name, { from_date: fromDate, to_date: toDate }),
    [name, fromDate, toDate]
  )
  if (loading) return <div className="text-zinc-700 text-xs py-4 text-center">Loading…</div>
  return (
    <div className="space-y-1">
      {(data || []).map(a => (
        <button key={a.album}
          onClick={() => navigate(`/explore/album/${encodeURIComponent(a.album)}`)}
          className="w-full flex items-center justify-between py-1.5 px-2 rounded-lg hover:bg-zinc-800 transition-colors text-left">
          <span className="text-sm text-zinc-300 truncate">{a.album}</span>
          <span className="text-xs text-zinc-500 shrink-0 ml-2">{a.plays} plays · {a.tracks} tracks</span>
        </button>
      ))}
    </div>
  )
}

function SimilarPanel({ name }) {
  const navigate = useNavigate()
  const { data, loading } = useChartData(() => analytics.artistSimilar(name), [name])
  if (loading) return <div className="text-zinc-700 text-xs py-4 text-center">Loading…</div>
  if (!data?.length) return <div className="text-zinc-700 text-xs py-4 text-center">No similar artist data yet</div>
  return (
    <div className="space-y-1">
      {data.map(a => (
        <button key={a.artist} onClick={() => navigate(`/explore/artist/${encodeURIComponent(a.artist)}`)}
          className="w-full flex items-center justify-between py-1.5 px-2 rounded-lg hover:bg-zinc-800 transition-colors text-left">
          <span className="text-sm text-zinc-300">{a.artist}</span>
          <span className="text-xs text-zinc-600">{Math.round(a.similarity * 100)}%</span>
        </button>
      ))}
    </div>
  )
}

const MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']

function TimelinePanel({ name }) {
  const { data, loading } = useChartData(() => analytics.artistTimeline(name), [name])
  if (loading) return <div className="text-zinc-700 text-xs py-4 text-center">Loading…</div>
  if (!data?.length) return <div className="text-zinc-700 text-xs py-4 text-center">No listening history found</div>

  const maxPlays = Math.max(...data.map(d => d.plays))

  return (
    <div className="space-y-1 pr-1">
      {data.slice().reverse().map(d => {
        const pct = maxPlays > 0 ? (d.plays / maxPlays) * 100 : 0
        const firstMonth = d.first_date ? MONTH_NAMES[new Date(d.first_date).getUTCMonth()] : ''
        return (
          <div key={d.year} className="flex items-center gap-3 py-1.5 px-2 rounded-lg hover:bg-zinc-800/60 transition-colors">
            <span className="text-xs font-mono text-zinc-500 w-10 shrink-0">{d.year}</span>
            <div className="flex-1 min-w-0">
              <div className="h-2 rounded-full bg-zinc-800 overflow-hidden">
                <div className="h-full rounded-full bg-violet-500/70" style={{ width: `${pct}%` }} />
              </div>
            </div>
            <span className="text-xs text-zinc-400 w-16 text-right shrink-0">{d.plays.toLocaleString()} plays</span>
            <span className="text-xs text-zinc-600 w-20 shrink-0 hidden sm:block truncate" title={d.first_track}>
              {firstMonth && `${firstMonth} · `}{d.unique_tracks}t
            </span>
          </div>
        )
      })}
    </div>
  )
}

function SessionsPanel({ name }) {
  const { data, loading } = useChartData(() => analytics.artistSessions(name), [name])
  const [expanded, setExpanded] = useState(null)
  if (loading) return <div className="text-zinc-700 text-xs py-4 text-center">Loading…</div>
  if (!data?.length) return <div className="text-zinc-700 text-xs py-4 text-center">No sessions found</div>

  return (
    <div className="space-y-0.5">
      {data.map((s, i) => {
        const isOpen = expanded === i
        const dur = s.duration_minutes
        const durStr = dur >= 60
          ? `${Math.floor(dur / 60)}h ${dur % 60}m`
          : dur > 0 ? `${dur}m` : `${s.track_count} track${s.track_count !== 1 ? 's' : ''}`
        return (
          <div key={i} className="rounded-lg overflow-hidden">
            <button
              onClick={() => setExpanded(isOpen ? null : i)}
              className="w-full flex items-center justify-between py-1.5 px-2 hover:bg-zinc-800 transition-colors text-left">
              <span className="text-xs text-zinc-400">{s.session_date}</span>
              <div className="flex items-center gap-3">
                <span className="text-xs text-zinc-600">{s.track_count} tracks · {durStr}</span>
                <span className="text-zinc-700 text-[10px]">{isOpen ? '▲' : '▼'}</span>
              </div>
            </button>
            {isOpen && (
              <div className="px-3 pb-2 space-y-0.5 bg-zinc-900/50">
                {(s.tracks || []).map((t, ti) => (
                  <div key={ti} className="text-xs text-zinc-500 py-0.5 truncate">{ti + 1}. {t}</div>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

const PANELS = {
  artist: ['Stats', 'Albums', 'Similar', 'Compare', 'Timeline', 'Sessions'],
  album:  ['Stats', 'Tracks', 'Compare'],
  track:  ['Stats', 'Compare'],
}

function HistoryChart({ type, name, fromDate, toDate, granularity, chartType, metric, annotationPoints }) {
  const fetcher = type === 'album'
    ? () => analytics.albumHistory(name, { from_date: fromDate, to_date: toDate, granularity, metric })
    : type === 'track'
      ? () => analytics.trackHistory(name, { from_date: fromDate, to_date: toDate, granularity, metric })
      : () => analytics.artistHistory(name, { from_date: fromDate, to_date: toDate, granularity, metric })

  return <ArtistHistoryChart
    name={name} fromDate={fromDate} toDate={toDate}
    granularity={granularity} chartType={chartType}
    metric={metric} annotationPoints={annotationPoints}
    _fetcher={fetcher}
  />
}

function AlbumTracksPanel({ name, fromDate, toDate }) {
  const navigate = useNavigate()
  const { data, loading } = useChartData(
    () => analytics.albumTracks(name, { from_date: fromDate, to_date: toDate }),
    [name, fromDate, toDate]
  )
  if (loading) return <div className="text-zinc-700 text-xs py-4 text-center">Loading…</div>
  return (
    <div className="space-y-1">
      {(data || []).map(t => (
        <button key={t.track}
          onClick={() => navigate(`/explore/track/${encodeURIComponent(t.track)}`)}
          className="w-full flex items-center justify-between py-1.5 px-2 rounded-lg hover:bg-zinc-800 transition-colors text-left">
          <span className="text-sm text-zinc-300 truncate">{t.track}</span>
          <span className="text-xs text-zinc-500 shrink-0 ml-2">{t.plays} plays</span>
        </button>
      ))}
    </div>
  )
}

function GenericStatsPanel({ type, name }) {
  const fetcher = type === 'album'
    ? () => analytics.albumStats(name)
    : () => analytics.trackStats(name)
  const { data, loading } = useChartData(fetcher, [name, type])
  if (loading) return <div className="text-zinc-700 text-xs">Loading stats…</div>
  if (!data) return <div className="text-zinc-700 text-xs">No stats yet — run a sync</div>
  return (
    <div className="grid grid-cols-1 gap-2 flex-1 auto-rows-fr">
      <StatPill label="All-time plays"  value={data.total_plays?.toLocaleString()} />
      <StatPill label="7d plays"        value={data.plays_7d} />
      <StatPill label="30d plays"       value={data.plays_30d} />
      <StatPill label="All-time rank"   value={data.rank_all_time ? `#${data.rank_all_time}` : null} />
      <StatPill label="First heard"     value={data.first_heard} />
      <StatPill label="Last heard"      value={data.last_heard} />
      <StatPill label="Days since last" value={data.days_since_last_heard != null ? `${Math.round(data.days_since_last_heard)}d ago` : null} />
      <StatPill label="Longest streak"  value={data.longest_streak_days ? `${data.longest_streak_days}d` : null} />
      <StatPill label="Peak week"       value={data.peak_week_plays ? `${data.peak_week_plays} plays` : null} />
    </div>
  )
}

function ComparePanel({ primaryName, primaryType, fromDate, toDate, granularity }) {
  const store = useUIStore()
  const compareEntities = store.deepDiveCompareEntities
  const addEntity = store.addCompareEntity
  const removeEntity = store.removeCompareEntity
  const [search, setSearch] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [hoveredIndex, setHoveredIndex] = useState(-1)
  const [allSeries, setAllSeries] = useState([])
  const [loading, setLoading] = useState(false)
  const searchRef = useRef(null)

  const allEntities = [
    { type: primaryType, id: primaryName, primary: true },
    ...compareEntities,
  ]

  useEffect(() => {
    if (!primaryName) return
    setLoading(true)
    const params = { from_date: fromDate, to_date: toDate, granularity, metric: 'plays' }
    // Fetch by each entity's type: on an album or track page this used to ask
    // for artist history under the album/track name, and plotted nothing.
    const history = { album: analytics.albumHistory, track: analytics.trackHistory }
    Promise.all(
      allEntities.map(e =>
        (history[e.type] ?? analytics.artistHistory)(e.id, params)
          .then(data => ({ name: e.id, data, primary: e.primary }))
          .catch(() => ({ name: e.id, data: [], primary: e.primary }))
      )
    ).then(results => {
      setAllSeries(results)
      setLoading(false)
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [primaryName, fromDate, toDate, granularity, compareEntities.length, compareEntities.map(e => e.id).join(',')])

  const options = merge({
    chart: { type: 'line', height: 260 },
    xAxis: { type: 'datetime', labels: { formatter() { return Highcharts.dateFormat('%b %Y', this.value) } } },
    yAxis: { min: 0 },
    legend: { enabled: true },
    tooltip: { shared: true, xDateFormat: '%b %e, %Y' },
    plotOptions: { line: { lineWidth: 2, marker: { enabled: false } } },
    series: allSeries.map((s, i) => ({
      name: s.name,
      color: COLORS[i % COLORS.length],
      lineWidth: s.primary ? 2.5 : 1.5,
      data: (s.data || []).map(d => [Date.parse(d.date), d.value ?? d.plays ?? 0]),
    })),
  })

  // Debounced artist search for suggestions
  useEffect(() => {
    if (search.length < 2) { setSuggestions([]); return }
    const t = setTimeout(() => {
      analytics.search(search, { limit: 8 })
        .then(results => setSuggestions(
          (results || []).filter(r => r.type === primaryType && !allEntities.find(e => e.id.toLowerCase() === r.name.toLowerCase()))
        ))
        .catch(() => setSuggestions([]))
    }, 200)
    return () => clearTimeout(t)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search])

  useEffect(() => { setHoveredIndex(-1) }, [suggestions])

  const handleAdd = (name) => {
    const n = (name ?? search).trim()
    if (!n || allEntities.find(e => e.id.toLowerCase() === n.toLowerCase())) return
    addEntity({ type: primaryType, id: n })
    setSearch('')
    setSuggestions([])
    setShowSuggestions(false)
  }

  return (
    <div className="space-y-3">
      {/* Entity chips */}
      <div className="flex flex-wrap gap-2">
        {allEntities.map((e, i) => (
          <div key={e.id} className="flex items-center gap-1.5 bg-zinc-800 rounded-full px-3 py-1">
            <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: COLORS[i % COLORS.length] }} />
            <span className="text-xs text-zinc-300">{e.id}</span>
            {!e.primary && (
              <button onClick={() => removeEntity(e.id)} className="text-zinc-600 hover:text-zinc-300 ml-1 text-xs leading-none">✕</button>
            )}
          </div>
        ))}
      </div>

      {/* Add entity input with suggestions */}
      {allEntities.length < 5 && (
        <div className="relative" ref={searchRef}>
          <div className="flex gap-2">
            <input
              value={search}
              onChange={e => { setSearch(e.target.value); setShowSuggestions(true) }}
              onKeyDown={e => {
                if (e.key === 'ArrowDown') {
                  e.preventDefault()
                  setHoveredIndex(i => Math.min(i + 1, suggestions.length - 1))
                  setShowSuggestions(true)
                } else if (e.key === 'ArrowUp') {
                  e.preventDefault()
                  setHoveredIndex(i => Math.max(i - 1, -1))
                } else if (e.key === 'Escape') {
                  setShowSuggestions(false)
                  setHoveredIndex(-1)
                } else if (e.key === 'Enter') {
                  if (hoveredIndex >= 0 && suggestions[hoveredIndex]) {
                    handleAdd(suggestions[hoveredIndex].name)
                  } else {
                    handleAdd()
                  }
                }
              }}
              onFocus={() => setShowSuggestions(true)}
              onBlur={() => setTimeout(() => { setShowSuggestions(false); setHoveredIndex(-1) }, 150)}
              placeholder="Add artist to compare…"
              className="flex-1 bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-1.5 text-xs text-zinc-200
                placeholder:text-zinc-600 focus:outline-none focus:border-violet-500"
            />
            <button onClick={() => handleAdd()} disabled={!search.trim()}
              className="bg-violet-600 hover:bg-violet-500 disabled:opacity-40 text-white rounded-lg px-3 py-1.5 text-xs transition-colors">
              Add
            </button>
          </div>
          {showSuggestions && suggestions.length > 0 && (
            <div className="absolute z-20 top-full left-0 right-0 mt-1 bg-zinc-900 border border-zinc-700 rounded-xl shadow-xl overflow-hidden">
              {suggestions.map((s, i) => (
                <button key={s.name} onMouseDown={() => handleAdd(s.name)}
                  onMouseEnter={() => setHoveredIndex(i)}
                  onMouseLeave={() => setHoveredIndex(-1)}
                  className={`w-full flex items-center justify-between px-3 py-2 text-xs text-zinc-300 transition-colors text-left ${
                    i === hoveredIndex ? 'bg-zinc-700' : 'hover:bg-zinc-800'
                  }`}>
                  <span>{s.name}</span>
                  <span className="text-zinc-600">{s.plays?.toLocaleString()} plays</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Multi-series chart */}
      {loading
        ? <div className="text-zinc-600 text-xs py-4 text-center">Loading…</div>
        : <HighchartsReact highcharts={Highcharts} options={options} />
      }
    </div>
  )
}

function EntitySearchBar({ currentType }) {
  const navigate = useNavigate()
  const [type, setType] = useState(currentType)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [open, setOpen] = useState(false)
  const [idx, setIdx] = useState(-1)

  useEffect(() => { setType(currentType) }, [currentType])

  useEffect(() => {
    if (query.length < 2) { setResults([]); return }
    const t = setTimeout(() => {
      analytics.search(query, { limit: 8 })
        .then(r => setResults((r || []).filter(x => x.type === type)))
        .catch(() => setResults([]))
    }, 200)
    return () => clearTimeout(t)
  }, [query, type])

  useEffect(() => { setIdx(-1) }, [results])

  const go = (name) => {
    navigate(`/explore/${type}/${encodeURIComponent(name)}`)
    setQuery('')
    setResults([])
    setOpen(false)
  }

  return (
    <div className="flex items-center gap-2">
      <div className="flex gap-0.5">
        {['artist', 'album', 'track'].map(t => (
          <button key={t} onClick={() => { setType(t); setQuery('') }}
            className={`px-2 py-1 rounded text-[11px] font-medium transition-colors capitalize ${
              type === t ? 'bg-zinc-700 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300'
            }`}>
            {t}
          </button>
        ))}
      </div>
      <div className="relative">
        <input
          value={query}
          onChange={e => { setQuery(e.target.value); setOpen(true) }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => { setOpen(false); setIdx(-1) }, 150)}
          onKeyDown={e => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setIdx(i => Math.min(i + 1, results.length - 1)) }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setIdx(i => Math.max(i - 1, -1)) }
            else if (e.key === 'Enter' && idx >= 0 && results[idx]) go(results[idx].name)
            else if (e.key === 'Escape') { setOpen(false); setIdx(-1) }
          }}
          placeholder={`Search ${type}s…`}
          className="w-48 bg-zinc-800 border border-zinc-700 rounded-lg px-3 py-1.5 text-xs text-zinc-200
            placeholder:text-zinc-600 focus:outline-none focus:border-violet-500"
        />
        {open && results.length > 0 && (
          <div className="absolute z-30 top-full right-0 mt-1 w-64 bg-zinc-900 border border-zinc-700 rounded-xl shadow-xl overflow-hidden">
            {results.map((r, i) => (
              <button key={r.name} onMouseDown={() => go(r.name)}
                onMouseEnter={() => setIdx(i)}
                onMouseLeave={() => setIdx(-1)}
                className={`w-full flex items-center justify-between px-3 py-2 text-xs text-zinc-300 transition-colors text-left ${
                  i === idx ? 'bg-zinc-700' : 'hover:bg-zinc-800'
                }`}>
                <span>{r.name}</span>
                <span className="text-zinc-600">{r.plays?.toLocaleString()} plays</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function ToggleGroup({ options, value, onChange, labels }) {
  return (
    <div className="flex gap-1">
      {options.map(o => (
        <button key={o} onClick={() => onChange(o)}
          className={`px-2 py-0.5 rounded text-[11px] font-medium transition-colors ${
            value === o ? 'bg-violet-600 text-white' : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800'
          }`}>
          {labels?.[o] ?? o}
        </button>
      ))}
    </div>
  )
}

export function DeepDive() {
  const { type = 'artist', id = '' } = useParams()
  const name = decodeURIComponent(id)
  const navigate = useNavigate()
  const store = useUIStore()

  const period       = store.deepDivePeriod
  const setPeriod    = store.setDeepDivePeriod
  const granOverride = store.deepDiveGranularity
  const setGran      = store.setDeepDiveGranularity
  const chartType    = store.deepDiveChartType
  const setChartType = store.setDeepDiveChartType
  const metric       = store.deepDiveMetric ?? 'plays'
  const setMetric    = store.setDeepDiveMetric
  const panel        = store.deepDivePanel ?? 'Stats'
  const setPanel     = store.setDeepDivePanel
  const annotations  = store.deepDiveAnnotations

  const { from_date: fromDate, to_date: toDate } = periodToDates(period)
  const gran = granOverride ?? AUTO_GRAN[period] ?? 'week'

  useEffect(() => {
    if (name && type) store.setLastViewedEntity({ type, id: name })
  }, [type, name])

  const { data: statsData } = useChartData(
    () => type === 'artist' ? analytics.artistStats(name) : Promise.resolve(null),
    [name, type]
  )
  const annotationPoints = annotations && statsData ? [
    statsData.first_heard && { date: statsData.first_heard, label: 'First heard' },
    statsData.peak_week_date && { date: statsData.peak_week_date, label: `Peak: ${statsData.peak_week_plays} plays` },
  ].filter(Boolean) : []

  const panelContent = (
    <>
      {panel === 'Stats'    && type === 'artist' && <StatsPanel name={name} />}
      {panel === 'Stats'    && type !== 'artist' && <GenericStatsPanel type={type} name={name} />}
      {panel === 'Albums'   && type === 'artist' && <AlbumsPanel name={name} fromDate={fromDate} toDate={toDate} />}
      {panel === 'Tracks'   && type === 'album'  && <AlbumTracksPanel name={name} fromDate={fromDate} toDate={toDate} />}
      {panel === 'Similar'  && type === 'artist' && <SimilarPanel name={name} />}
      {panel === 'Timeline' && type === 'artist' && <TimelinePanel name={name} />}
      {panel === 'Sessions' && type === 'artist' && <SessionsPanel name={name} />}
    </>
  )

  return (
    <AnalyticsShell>
      <div className="h-full overflow-hidden xl:grid xl:grid-cols-[1fr_560px] xl:divide-x xl:divide-zinc-800">

        {/* ── Left column: charts ── */}
        <div className="h-full overflow-y-auto">
          <div className="px-6 py-5 space-y-4">

            {/* Header: entity name + search */}
            <div className="flex items-start justify-between gap-4 flex-wrap">
              <div>
                <button
                  onClick={() => navigate(-1)}
                  className="flex items-center gap-1 text-xs text-zinc-600 hover:text-zinc-400 transition-colors mb-1.5 group"
                >
                  <span className="group-hover:-translate-x-0.5 transition-transform">←</span>
                  <span>back</span>
                </button>
                <p className="text-xs text-zinc-600 uppercase tracking-wide">{type}</p>
                <h1 className="text-xl font-semibold text-zinc-100 mt-0.5">{name}</h1>
              </div>
              <EntitySearchBar currentType={type} />
            </div>

            {/* Controls */}
            <div className="flex flex-wrap gap-2 items-center">
              <div className="flex gap-1">
                {PERIODS.map(p => (
                  <button key={p} onClick={() => { setPeriod(p); setGran(null) }}
                    className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-colors ${
                      period === p ? 'bg-violet-600 text-white' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'
                    }`}>
                    {p}
                  </button>
                ))}
              </div>
              <ToggleGroup options={GRAN_OPTIONS} value={gran} onChange={setGran} labels={{ day: 'D', week: 'W', month: 'M', year: 'Y' }} />
              <ToggleGroup options={CHART_TYPES} value={chartType} onChange={setChartType} labels={{ line: 'Line', area: 'Area', bar: 'Bar' }} />
              <ToggleGroup options={METRIC_OPTIONS} value={metric} onChange={setMetric} labels={{ plays: 'Plays', unique_tracks: 'Tracks' }} />
              <button onClick={() => store.setDeepDiveAnnotations(!annotations)}
                className={`px-2 py-0.5 rounded text-[11px] font-medium transition-colors ${
                  annotations ? 'bg-zinc-700 text-zinc-200' : 'text-zinc-600 hover:text-zinc-400'
                }`}>
                Annot.
              </button>
            </div>

            {/* History chart */}
            <div className={`rounded-2xl transition-all duration-500 ${
              store.highlightedChart === 'deepdive_chart'
                ? 'ring-2 ring-violet-400/70 ring-offset-1 ring-offset-zinc-950 shadow-[0_0_24px_rgba(139,92,246,0.3)]'
                : ''
            }`}>
              <HistoryChart type={type} name={name} fromDate={fromDate} toDate={toDate} granularity={gran} chartType={chartType} metric={metric} annotationPoints={annotationPoints} />
            </div>

            {/* Panel tabs — mobile only; desktop uses right column */}
            <div className="xl:hidden bg-zinc-900 rounded-2xl border border-zinc-800">
              <div className="flex flex-wrap gap-1 px-4 pt-4 pb-2 border-b border-zinc-800">
                {(PANELS[type] ?? PANELS.artist).map(p => (
                  <button key={p} onClick={() => setPanel(panel === p ? null : p)}
                    className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                      panel === p ? 'bg-violet-600 text-white' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'
                    }`}>
                    {p}
                  </button>
                ))}
              </div>
              {panel && panel !== 'Compare' && <div className="p-4">{panelContent}</div>}
              {panel === 'Compare' && (
                <div className="p-4">
                  <ComparePanel primaryName={name} primaryType={type} fromDate={fromDate} toDate={toDate} granularity={gran} />
                </div>
              )}
            </div>

            {/* Compare chart — always below history */}
            <div className="bg-zinc-900 rounded-2xl border border-zinc-800">
              <div className="px-4 pt-4 pb-2 border-b border-zinc-800">
                <p className="text-xs text-zinc-500 uppercase tracking-wide">Compare</p>
              </div>
              <div className="p-4">
                <ComparePanel primaryName={name} primaryType={type} fromDate={fromDate} toDate={toDate} granularity={gran} />
              </div>
            </div>

          </div>
        </div>

        {/* ── Right column: panel details (desktop only) ── */}
        <div className="hidden xl:flex xl:flex-col h-full overflow-hidden">
          <div className="flex-1 flex flex-col min-h-0 px-6 py-5">
            <div className="flex-1 flex flex-col min-h-0 bg-zinc-900 rounded-2xl border border-zinc-800 overflow-hidden">
              <div className="shrink-0 flex flex-wrap gap-1 px-4 pt-4 pb-2 border-b border-zinc-800">
                {(PANELS[type] ?? PANELS.artist).filter(p => p !== 'Compare').map(p => (
                  <button key={p} onClick={() => setPanel(panel === p ? null : p)}
                    className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                      panel === p ? 'bg-violet-600 text-white' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'
                    }`}>
                    {p}
                  </button>
                ))}
              </div>
              {panel && panel !== 'Compare' && (
                <div className="flex-1 flex flex-col min-h-0 overflow-y-auto p-4">{panelContent}</div>
              )}
            </div>
          </div>
        </div>

      </div>
    </AnalyticsShell>
  )
}
