// The reading log: what I've read, when, and what I made of it.
//
// Deliberately not a Goodreads clone. Goodreads keeps the catalogue; this keeps
// the record — the same job the guitar practice log does for songs, with the
// sorting and filtering a table gives you. A Goodreads CSV export updates the
// whole library in place; anything typed here is kept separate and never
// overwritten by an import.
import { useEffect, useMemo, useRef, useState } from 'react'
import { books as booksApi } from '../../api'

const SHELVES = [
  { id: 'all', label: 'Everything' },
  { id: 'read', label: 'Read' },
  { id: 'currently-reading', label: 'Reading' },
  { id: 'to-read', label: 'Want to read' },
]

const COLUMNS = [
  { id: 'title', label: 'Title', sort: (b) => b.title?.toLowerCase() ?? '' },
  { id: 'author', label: 'Author', sort: (b) => b.author?.toLowerCase() ?? '' },
  { id: 'rating', label: 'Rating', sort: (b) => b.rating ?? -1, align: 'right' },
  { id: 'date_started', label: 'Started', sort: (b) => b.date_started ?? '' },
  { id: 'date_read', label: 'Finished', sort: (b) => b.date_read ?? '' },
  { id: 'num_pages', label: 'Pages', sort: (b) => b.num_pages ?? -1, align: 'right' },
  { id: 'exclusive_shelf', label: 'Shelf', sort: (b) => b.exclusive_shelf ?? '' },
]

// Open Library serves covers by ISBN with no key; default=false 404s instead of
// returning a placeholder, so the tile can fall back to something of its own.
const coverUrl = (b) => {
  const isbn = (b.isbn13 || b.isbn || '').replace(/[^0-9Xx]/g, '')
  return isbn ? `https://covers.openlibrary.org/b/isbn/${isbn}-M.jpg?default=false` : null
}

const HUES = [265, 199, 340, 152, 28, 218, 291, 12]
const hueFor = (s = '') => {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0
  return HUES[h % HUES.length]
}

const fmtDate = (d) => (d ? new Date(d + 'T00:00:00').toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—')

function Stars({ value, onChange }) {
  const stars = [1, 2, 3, 4, 5]
  return (
    <span className="inline-flex gap-0.5">
      {stars.map(n => (
        <button
          key={n}
          disabled={!onChange}
          onClick={() => onChange?.(n === value ? 0 : n)}
          className={`${onChange ? 'cursor-pointer hover:text-amber-300' : 'cursor-default'} ${n <= (value ?? 0) ? 'text-amber-400' : 'text-zinc-700'} text-sm leading-none`}
          title={onChange ? `${n} star${n > 1 ? 's' : ''}` : undefined}
        >★</button>
      ))}
    </span>
  )
}

function Cover({ book, className = '' }) {
  const [broken, setBroken] = useState(false)
  const src = coverUrl(book)
  if (src && !broken) {
    return <img src={src} alt="" loading="lazy" onError={() => setBroken(true)}
                className={`object-cover w-full h-full ${className}`} />
  }
  const hue = hueFor(book.title)
  return (
    <div className={`w-full h-full flex items-end p-2 ${className}`}
         style={{ background: `linear-gradient(150deg, hsl(${hue} 40% 28%), hsl(${hue} 38% 14%))` }}>
      <span className="text-[10px] leading-tight text-white/80 line-clamp-4">{book.title}</span>
    </div>
  )
}

function Editor({ book, onSave, onDelete, onClose }) {
  const [draft, setDraft] = useState(book)
  useEffect(() => setDraft(book), [book])
  const set = (k) => (e) => setDraft(d => ({ ...d, [k]: e.target.value === '' ? null : e.target.value }))
  const field = 'w-full bg-zinc-900 border border-zinc-700 rounded-lg px-2.5 py-1.5 text-sm text-zinc-200 focus:outline-none focus:border-violet-500'

  return (
    <aside className="fixed inset-y-0 right-0 w-full sm:w-[380px] bg-zinc-950 border-l border-zinc-800 z-40 flex flex-col">
      <header className="flex items-center justify-between px-5 py-4 border-b border-zinc-800">
        <h3 className="text-sm font-medium text-zinc-200">{book.book_id?.startsWith('manual-') ? 'Book' : 'Book · from Goodreads'}</h3>
        <button onClick={onClose} className="text-zinc-500 hover:text-zinc-200 text-lg leading-none">✕</button>
      </header>
      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
        <div className="flex gap-3">
          <div className="w-20 h-28 rounded-md overflow-hidden shrink-0 bg-zinc-800"><Cover book={draft} /></div>
          <div className="flex-1 space-y-2">
            <input className={field} value={draft.title ?? ''} onChange={set('title')} placeholder="Title" />
            <input className={field} value={draft.author ?? ''} onChange={set('author')} placeholder="Author" />
          </div>
        </div>
        <div>
          <label className="block text-[11px] uppercase tracking-wide text-zinc-500 mb-1">Rating</label>
          <Stars value={draft.rating} onChange={(n) => setDraft(d => ({ ...d, rating: n }))} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-[11px] uppercase tracking-wide text-zinc-500 mb-1">Started</label>
            <input type="date" className={field} value={draft.date_started ?? ''} onChange={set('date_started')} />
          </div>
          <div>
            <label className="block text-[11px] uppercase tracking-wide text-zinc-500 mb-1">Finished</label>
            <input type="date" className={field} value={draft.date_read ?? ''} onChange={set('date_read')} />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-[11px] uppercase tracking-wide text-zinc-500 mb-1">Shelf</label>
            <select className={field} value={draft.exclusive_shelf ?? 'read'} onChange={set('exclusive_shelf')}>
              {SHELVES.filter(s => s.id !== 'all').map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-[11px] uppercase tracking-wide text-zinc-500 mb-1">Pages</label>
            <input type="number" className={field} value={draft.num_pages ?? ''} onChange={set('num_pages')} />
          </div>
        </div>
        <div>
          <label className="block text-[11px] uppercase tracking-wide text-zinc-500 mb-1">Notes</label>
          <textarea rows={5} className={field} value={draft.notes ?? ''} onChange={set('notes')}
                    placeholder="What stayed with you?" />
          <p className="text-[10px] text-zinc-600 mt-1">Notes and the start date are yours — a Goodreads re-import leaves them alone.</p>
        </div>
        {draft.ol_description && (
          <details className="text-xs text-zinc-500">
            <summary className="cursor-pointer text-zinc-400">Open Library blurb</summary>
            <p className="mt-1 leading-relaxed">{draft.ol_description}</p>
          </details>
        )}
        {draft.ol_subjects?.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {draft.ol_subjects.slice(0, 8).map(s => (
              <span key={s} className="px-1.5 py-0.5 rounded bg-zinc-800 text-[10px] text-zinc-400">{s}</span>
            ))}
          </div>
        )}
      </div>
      <footer className="flex items-center gap-2 px-5 py-4 border-t border-zinc-800">
        <button onClick={() => onSave(draft)}
                className="px-3 py-1.5 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium">Save</button>
        <button onClick={onClose} className="px-3 py-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 text-sm">Cancel</button>
        <button onClick={() => onDelete(book)} className="ml-auto text-xs text-zinc-600 hover:text-red-400">Remove</button>
      </footer>
    </aside>
  )
}

export function BooksPage() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [shelf, setShelf] = useState('all')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState({ key: 'date_read', dir: 'desc' })
  const [view, setView] = useState('shelf')
  const [editing, setEditing] = useState(null)
  const [importing, setImporting] = useState(null)
  const fileRef = useRef(null)

  const load = async () => {
    try { setData(await booksApi.list()) } catch (e) { setError(e.message) }
  }
  useEffect(() => { load() }, [])

  const shown = useMemo(() => {
    const all = data?.books ?? []
    const q = search.trim().toLowerCase()
    const col = COLUMNS.find(c => c.id === sort.key) ?? COLUMNS[4]
    const filtered = all.filter(b =>
      (shelf === 'all' || b.exclusive_shelf === shelf) &&
      (!q || b.title?.toLowerCase().includes(q) || b.author?.toLowerCase().includes(q))
    )
    return [...filtered].sort((a, b) => {
      const av = col.sort(a), bv = col.sort(b)
      if (av === bv) return (a.title ?? '').localeCompare(b.title ?? '')
      return (av > bv ? 1 : -1) * (sort.dir === 'asc' ? 1 : -1)
    })
  }, [data, shelf, search, sort])

  const save = async (draft) => {
    const patch = {
      title: draft.title, author: draft.author, rating: Number(draft.rating) || 0,
      date_started: draft.date_started || null, date_read: draft.date_read || null,
      exclusive_shelf: draft.exclusive_shelf, notes: draft.notes || null,
      num_pages: draft.num_pages ? Number(draft.num_pages) : null,
    }
    if (draft.book_id) await booksApi.update(draft.book_id, patch)
    else await booksApi.add(patch)
    setEditing(null)
    load()
  }

  const remove = async (book) => {
    if (!window.confirm(`Remove “${book.title}” from the log?`)) return
    await booksApi.remove(book.book_id)
    setEditing(null)
    load()
  }

  // The import runs in the backend behind a 202, so poll until it stops.
  const runImport = async (file) => {
    setImporting({ state: 'running', name: file.name })
    try {
      await booksApi.importGoodreads(file)
      for (let i = 0; i < 600; i++) {
        await new Promise(r => setTimeout(r, 2000))
        const status = await booksApi.importStatus()
        const gr = status?.goodreads ?? {}
        if (!gr.process_running) {
          setImporting(gr.last_error ? { state: 'error', message: gr.last_error } : { state: 'done', count: gr.book_count })
          load()
          return
        }
      }
      setImporting({ state: 'error', message: 'still running after 20 minutes' })
    } catch (e) {
      setImporting({ state: 'error', message: e.message })
    }
  }

  const counts = data?.counts
  const sortBy = (key) => setSort(s => ({ key, dir: s.key === key && s.dir === 'desc' ? 'asc' : 'desc' }))
  const control = 'px-2.5 py-1.5 rounded-lg bg-zinc-900 border border-zinc-800 text-xs text-zinc-300 focus:outline-none focus:border-violet-500'

  return (
    <div className="h-full overflow-y-auto">
      <div className="px-6 py-5 max-w-7xl mx-auto">
        {/* Summary */}
        <div className="flex flex-wrap items-end gap-x-6 gap-y-2 mb-5">
          {counts && [
            ['Books', counts.total],
            ['Read', counts.shelves?.read ?? 0],
            ['Pages read', counts.pages_read?.toLocaleString()],
            ['Average rating', counts.average_rating ?? '—'],
          ].map(([label, value]) => (
            <div key={label}>
              <div className="text-[10px] uppercase tracking-wide text-zinc-600">{label}</div>
              <div className="text-xl text-zinc-100 font-semibold">{value}</div>
            </div>
          ))}
          <div className="ml-auto flex items-center gap-2">
            <button onClick={() => setEditing({ exclusive_shelf: 'read', rating: 0 })}
                    className="px-3 py-1.5 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-xs font-medium">+ Add book</button>
            <input ref={fileRef} type="file" accept=".csv" className="hidden"
                   onChange={(e) => { const f = e.target.files?.[0]; if (f) runImport(f); e.target.value = '' }} />
            <button onClick={() => fileRef.current?.click()}
                    className="px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs font-medium">
              Import Goodreads CSV
            </button>
          </div>
        </div>

        {importing && (
          <div className={`mb-4 rounded-xl px-4 py-2.5 text-xs border ${
            importing.state === 'error' ? 'bg-red-950/60 border-red-800 text-red-300'
              : importing.state === 'done' ? 'bg-emerald-950/50 border-emerald-800 text-emerald-300'
                : 'bg-zinc-900 border-zinc-800 text-zinc-400'}`}>
            {importing.state === 'running' && <>Importing {importing.name}… Open Library lookups make this slow; the library updates when it finishes.</>}
            {importing.state === 'done' && <>Import finished — {importing.count} books in the library. Your notes and start dates were left alone.</>}
            {importing.state === 'error' && <>Import failed: {importing.message}</>}
          </div>
        )}

        {/* Controls */}
        <div className="flex flex-wrap items-center gap-2 mb-4">
          <div className="flex gap-1">
            {SHELVES.map(s => (
              <button key={s.id} onClick={() => setShelf(s.id)}
                      className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                        shelf === s.id ? 'bg-violet-600 text-white' : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'}`}>
                {s.label}{counts?.shelves?.[s.id] != null && s.id !== 'all' ? ` ${counts.shelves[s.id]}` : ''}
              </button>
            ))}
          </div>
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search title or author…"
                 className={`${control} w-52`} />
          <select className={control} value={sort.key} onChange={(e) => setSort(s => ({ ...s, key: e.target.value }))}>
            {COLUMNS.map(c => <option key={c.id} value={c.id}>Sort: {c.label}</option>)}
          </select>
          <button onClick={() => setSort(s => ({ ...s, dir: s.dir === 'desc' ? 'asc' : 'desc' }))} className={control}>
            {sort.dir === 'desc' ? '↓ Desc' : '↑ Asc'}
          </button>
          <div className="ml-auto flex gap-1">
            {['shelf', 'table'].map(v => (
              <button key={v} onClick={() => setView(v)}
                      className={`px-3 py-1.5 rounded-lg text-xs font-medium capitalize ${
                        view === v ? 'bg-zinc-700 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300'}`}>{v}</button>
            ))}
          </div>
        </div>

        {error && <p className="text-sm text-red-400">Could not load books: {error}</p>}
        {!data ? (
          <p className="text-zinc-600 text-sm py-10 text-center">Loading…</p>
        ) : shown.length === 0 ? (
          <p className="text-zinc-600 text-sm py-10 text-center">Nothing matches.</p>
        ) : view === 'shelf' ? (
          <div className="grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(120px,1fr))]">
            {shown.map(b => (
              <button key={b.book_id} onClick={() => setEditing(b)} className="text-left group">
                <div className="aspect-[2/3] rounded-lg overflow-hidden bg-zinc-800 ring-1 ring-white/5 group-hover:ring-violet-500/60 transition-all">
                  <Cover book={b} />
                </div>
                <div className="mt-1.5">
                  <div className="text-[11px] text-zinc-200 leading-tight line-clamp-2 group-hover:text-violet-300 transition-colors">{b.title}</div>
                  <div className="text-[10px] text-zinc-500 truncate">{b.author}</div>
                  <div className="mt-0.5 flex items-center gap-1.5">
                    <Stars value={b.rating} />
                    <span className="text-[10px] text-zinc-600">{b.date_read ? new Date(b.date_read).getFullYear() : ''}</span>
                  </div>
                </div>
              </button>
            ))}
          </div>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-zinc-800">
            <table className="w-full text-sm">
              <thead className="bg-zinc-900 text-zinc-400">
                <tr>
                  <th className="w-10"></th>
                  {COLUMNS.map(c => (
                    <th key={c.id} onClick={() => sortBy(c.id)}
                        className={`px-3 py-2 font-medium cursor-pointer select-none whitespace-nowrap hover:text-zinc-200 ${c.align === 'right' ? 'text-right' : 'text-left'}`}>
                      {c.label}{sort.key === c.id ? (sort.dir === 'desc' ? ' ↓' : ' ↑') : ''}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {shown.map(b => (
                  <tr key={b.book_id} onClick={() => setEditing(b)}
                      className="border-t border-zinc-800/80 hover:bg-zinc-900/60 cursor-pointer">
                    <td className="px-2 py-1.5"><div className="w-7 h-10 rounded overflow-hidden bg-zinc-800"><Cover book={b} /></div></td>
                    <td className="px-3 py-1.5 text-zinc-200">{b.title}</td>
                    <td className="px-3 py-1.5 text-zinc-400">{b.author}</td>
                    <td className="px-3 py-1.5 text-right"><Stars value={b.rating} /></td>
                    <td className="px-3 py-1.5 text-zinc-400 whitespace-nowrap">{fmtDate(b.date_started)}</td>
                    <td className="px-3 py-1.5 text-zinc-400 whitespace-nowrap">{fmtDate(b.date_read)}</td>
                    <td className="px-3 py-1.5 text-right text-zinc-400">{b.num_pages ?? '—'}</td>
                    <td className="px-3 py-1.5 text-zinc-500">{b.exclusive_shelf}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-[11px] text-zinc-600 mt-3">{shown.length} of {data?.books?.length ?? 0} shown</p>
      </div>

      {editing && <Editor book={editing} onSave={save} onDelete={remove} onClose={() => setEditing(null)} />}
    </div>
  )
}
