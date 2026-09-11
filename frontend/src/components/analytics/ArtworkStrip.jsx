import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { analytics } from '../../api'
import { useChartData } from './charts/useChartData'

// Deterministic tile colour for entities with no artwork, so the same artist
// always gets the same shade instead of flickering between renders.
const FALLBACK_HUES = [265, 199, 340, 152, 28, 218, 291, 12]

function hueFor(name) {
  let h = 0
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0
  return FALLBACK_HUES[h % FALLBACK_HUES.length]
}

function initials(name) {
  return name
    .replace(/^(the|a|an)\s+/i, '')
    .split(/\s+/)
    .slice(0, 2)
    .map(w => w[0])
    .join('')
    .toUpperCase()
}

// Backend returns a path relative to its image root; nginx/vite both map
// /api/* onto the backend with the prefix stripped. The static demo has no
// backend, so its fixtures carry image_url (the artwork's CDN source) instead.
const imageUrl = (p) => (p ? `/api/images/${p}` : null)

function Tile({ item, entity, onOpen }) {
  const [broken, setBroken] = useState(false)
  const label = item.name
  const sub = entity === 'album' ? item.artist : `${item.plays.toLocaleString()} plays`
  const src = item.image_url || imageUrl(item.image_path)
  const showImage = src && !broken

  return (
    <button
      onClick={() => onOpen(item)}
      title={entity === 'album' ? `${item.name} — ${item.artist}` : item.name}
      className="group/tile shrink-0 w-[104px] text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 rounded-lg"
    >
      <div className="relative w-[104px] h-[104px] rounded-lg overflow-hidden bg-zinc-800 ring-1 ring-white/5 group-hover/tile:ring-violet-500/60 transition-all duration-200">
        {showImage ? (
          <img
            src={src}
            alt=""
            loading="lazy"
            onError={() => setBroken(true)}
            className="w-full h-full object-cover group-hover/tile:scale-105 transition-transform duration-300"
          />
        ) : (
          <div
            className="w-full h-full flex items-center justify-center"
            style={{ background: `linear-gradient(140deg, hsl(${hueFor(label)} 40% 26%), hsl(${hueFor(label)} 35% 14%))` }}
          >
            <span className="text-lg font-semibold text-white/70 tracking-wide">{initials(label)}</span>
          </div>
        )}
        {/* Play count reads over the art itself; the gradient keeps it legible
            on both bright covers and dark ones. */}
        {entity === 'album' && (
          <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 to-transparent px-1.5 pt-4 pb-1">
            <span className="text-[10px] font-medium text-white/90">{item.plays.toLocaleString()}</span>
          </div>
        )}
      </div>
      <div className="mt-1.5 px-0.5">
        <div className="text-[11px] leading-tight text-zinc-200 truncate group-hover/tile:text-violet-300 transition-colors">{label}</div>
        <div className="text-[10px] leading-tight text-zinc-500 truncate">{sub}</div>
      </div>
    </button>
  )
}

export function ArtworkStrip({ period = '90d' }) {
  const [entity, setEntity] = useState('artist')
  const navigate = useNavigate()

  const { data, loading, error } = useChartData(
    () => analytics.topVisuals({ period, entity, limit: 20 }),
    [period, entity]
  )

  const onOpen = (item) => {
    const id = entity === 'album' ? item.name : item.name
    navigate(`/explore/${entity}/${encodeURIComponent(id)}`)
  }

  const items = data || []
  const withArt = items.filter(i => i.image_path).length

  return (
    <div className="mb-5">
      <div className="flex items-center justify-between mb-2.5">
        <div className="flex items-baseline gap-2.5">
          <h2 className="text-sm font-semibold text-zinc-200">
            {entity === 'album' ? 'Albums on rotation' : 'Artists on rotation'}
          </h2>
          <span className="text-[10px] text-zinc-600 uppercase tracking-wide">{period}</span>
        </div>
        <div className="flex items-center gap-1">
          {['artist', 'album'].map(e => (
            <button
              key={e}
              onClick={() => setEntity(e)}
              className={`px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors ${
                entity === e ? 'bg-zinc-700 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              {e === 'artist' ? 'Artists' : 'Albums'}
            </button>
          ))}
        </div>
      </div>

      {error ? (
        <div className="text-[11px] text-zinc-600 py-6">
          Artwork unavailable — run the visuals pipeline to populate it.
        </div>
      ) : loading ? (
        <div className="flex gap-3 overflow-hidden">
          {Array.from({ length: 10 }).map((_, i) => (
            <div key={i} className="shrink-0 w-[104px]">
              <div className="w-[104px] h-[104px] rounded-lg bg-zinc-800/60 animate-pulse" />
              <div className="mt-1.5 h-2.5 w-16 rounded bg-zinc-800/60 animate-pulse" />
            </div>
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="text-[11px] text-zinc-600 py-6">Nothing played in this period.</div>
      ) : (
        <>
          {/* Horizontal scroll keeps the strip to one row at any width — the
              dashboard below is the main event, this is a glance. */}
          <div className="flex gap-3 overflow-x-auto pb-1 -mx-1 px-1 [scrollbar-width:thin]">
            {items.map(item => (
              <Tile key={`${item.name}|${item.artist ?? ''}`} item={item} entity={entity} onOpen={onOpen} />
            ))}
          </div>
          {withArt < items.length && (
            <div className="text-[10px] text-zinc-600 mt-1.5">
              {items.length - withArt} of {items.length} missing artwork — initials shown instead.
            </div>
          )}
        </>
      )}
    </div>
  )
}
