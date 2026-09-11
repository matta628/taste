// Lines from the lyrics of tracks actually in rotation, cycling.
//
// Two shapes off the same data: LyricsCarousel sits in the Dashboard grid where
// the mood donut used to, and LyricsRail runs down the page margins on wide
// screens. The rail's cards are staggered so they never all turn at once.
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { analytics } from '../../../api'
import { artworkUrl } from '../artwork'
import { useChartData } from './useChartData'

const CYCLE_MS = 7000

function useLyricLines(limit) {
  return useChartData(() => analytics.lyricLines({ limit }), [limit])
}

// Walks a shuffled list, pausing on each entry. `offset` staggers the rail.
function useRotation(items, ms = CYCLE_MS, offset = 0) {
  const [idx, setIdx] = useState(0)
  const [shown, setShown] = useState(true)
  useEffect(() => {
    if (items.length < 2) return
    let fade, interval
    const tick = () => {
      setShown(false)   // fade out, swap while invisible, fade back in
      fade = setTimeout(() => { setIdx(i => (i + 1) % items.length); setShown(true) }, 450)
    }
    const start = setTimeout(() => { tick(); interval = setInterval(tick, ms) }, offset)
    return () => { clearTimeout(start); clearTimeout(fade); clearInterval(interval) }
  }, [items.length, ms, offset])
  return [items[idx % Math.max(items.length, 1)], shown]
}

function Attribution({ item, onOpen, className = '' }) {
  return (
    <button
      onClick={() => onOpen(item.artist)}
      className={`group/attr flex items-center gap-2 text-left ${className}`}
      title={`${item.artist} — ${item.track}`}
    >
      {artworkUrl(item) && (
        <img src={artworkUrl(item)} alt="" loading="lazy" className="w-7 h-7 rounded object-cover shrink-0" />
      )}
      <span className="min-w-0">
        <span className="block text-[11px] text-zinc-300 truncate group-hover/attr:text-violet-300 transition-colors">
          {item.track}
        </span>
        <span className="block text-[10px] text-zinc-500 truncate">{item.artist}</span>
      </span>
    </button>
  )
}

export function LyricsCarousel() {
  const { data, loading } = useLyricLines(60)
  const items = data || []
  const [item, shown] = useRotation(items)
  const navigate = useNavigate()
  const openArtist = (artist) => navigate(`/explore/artist/${encodeURIComponent(artist)}`)

  return (
    <div className="bg-zinc-900 rounded-2xl p-4 h-full min-h-[280px] flex flex-col">
      <div className="flex items-baseline justify-between mb-1">
        <h3 className="text-xs uppercase tracking-wide text-zinc-500">In your head</h3>
        <span className="text-[10px] text-zinc-600">{items.length} lines</span>
      </div>
      {loading ? (
        <div className="flex-1 flex items-center justify-center">
          <div className="h-3 w-40 rounded bg-zinc-800 animate-pulse" />
        </div>
      ) : !item ? (
        <div className="flex-1 flex items-center justify-center text-[11px] text-zinc-600 text-center px-4">
          No lyrics fetched yet — run the lyrics pipeline.
        </div>
      ) : (
        <div className="flex-1 flex flex-col justify-center gap-4 py-2">
          <blockquote
            className="transition-opacity duration-500 text-center px-2"
            style={{ opacity: shown ? 1 : 0 }}
          >
            {item.lines.map((line, i) => (
              <p key={i} className="text-[15px] leading-snug text-zinc-200 font-medium">{line}</p>
            ))}
          </blockquote>
          <div
            className="flex justify-center transition-opacity duration-500"
            style={{ opacity: shown ? 1 : 0 }}
          >
            <Attribution item={item} onOpen={openArtist} />
          </div>
        </div>
      )}
    </div>
  )
}

export function LyricsRail({ count = 3, side = 'left' }) {
  const { data } = useLyricLines(40)
  const items = data || []
  const navigate = useNavigate()
  // Each card takes its own slice of the pool, so the rail never shows a line
  // twice — and the right rail starts halfway in, or the two rails mirror each
  // other whenever the pool is a fixed list (as it is in the static demo).
  const half = Math.floor(items.length / 2)
  const pool = side === 'right' ? [...items.slice(half), ...items.slice(0, half)] : items
  const slices = Array.from({ length: count }, (_, i) => {
    const size = Math.floor(pool.length / count) || 1
    return pool.slice(i * size, (i + 1) * size)
  })

  if (!items.length) return null

  return (
    <div className="flex flex-col gap-4 pt-2">
      <div className="text-[10px] uppercase tracking-widest text-zinc-700 px-1">
        {side === 'left' ? 'On rotation' : 'Lines'}
      </div>
      {slices.map((slice, i) => (
        <RailCard key={i} items={slice} offset={i * 1800}
                  onOpen={(a) => navigate(`/explore/artist/${encodeURIComponent(a)}`)} />
      ))}
    </div>
  )
}

function RailCard({ items, offset, onOpen }) {
  const [item, shown] = useRotation(items, CYCLE_MS + 1500, offset)
  if (!item) return null
  return (
    <div className="rounded-xl bg-zinc-900/60 border border-zinc-800/80 p-3">
      <div className="transition-opacity duration-500" style={{ opacity: shown ? 1 : 0 }}>
        {item.lines.map((line, i) => (
          <p key={i} className="text-[12px] leading-snug text-zinc-300">{line}</p>
        ))}
        <Attribution item={item} onOpen={onOpen} className="mt-2.5" />
      </div>
    </div>
  )
}
