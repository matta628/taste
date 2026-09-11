// One app, four groups: Analytics, Chat, Books, Guitar.
//
// Each group is a single pill showing where you are inside it. Hovering (or
// tapping, on a touch screen) expands it sideways to the rest of that group's
// pages, current one first. Everything that used to be buried in the guitar
// app's sidebar — the chat, the playlist builder, the mood review, sync — is a
// route under one of these.
import { useState } from 'react'
import { Link, Outlet, useLocation } from 'react-router-dom'
import { DemoBanner } from './DemoBanner'
import { StaleBanner } from './StaleBanner'
import { LyricsTape } from './LyricsTape'
import { useLyrics } from './useLyrics'
import { useChatContext } from './ChatContext'

export const GROUPS = [
  {
    id: 'analytics',
    items: [
      { to: '/dashboard', label: 'Dashboard' },
      { to: '/explore', label: 'Deep Dive' },
      { to: '/discover', label: 'Discover' },
      { to: '/timemachine', label: 'Time Machine' },
      { to: '/mood', label: 'Mood review' },
    ],
  },
  {
    id: 'chat',
    items: [
      { to: '/chat', label: 'Chat' },
      { to: '/playlist', label: 'Playlist' },
    ],
  },
  { id: 'books', items: [{ to: '/books', label: 'Books' }] },
  {
    id: 'guitar',
    items: [
      { to: '/guitar', label: 'Library' },
      { to: '/guitar/practice', label: 'Practice' },
      { to: '/guitar/add', label: 'Add song' },
    ],
  },
]

const matches = (pathname, to) =>
  pathname === to || (to !== '/guitar' && pathname.startsWith(to + '/'))

function NavGroup({ group, pathname, expanded, onExpand, streaming }) {
  const activeIdx = group.items.findIndex(i => matches(pathname, i.to))
  const isActive = activeIdx >= 0
  // Current page first, so a collapsed group still says where you are.
  const items = isActive
    ? [group.items[activeIdx], ...group.items.filter((_, i) => i !== activeIdx)]
    : group.items

  return (
    <div
      className={`flex items-center rounded-xl p-0.5 transition-colors ${isActive ? 'bg-zinc-800/80' : 'bg-zinc-900/60'}`}
      onMouseEnter={() => onExpand(group.id)}
      onMouseLeave={() => onExpand(null)}
      onFocus={() => onExpand(group.id)}
    >
      {items.map((item, i) => {
        const current = matches(pathname, item.to)
        const hidden = i > 0 && !expanded
        return (
          <Link
            key={item.to}
            to={item.to}
            onClick={() => onExpand(null)}
            tabIndex={hidden ? -1 : 0}
            aria-hidden={hidden}
            className={`relative overflow-hidden whitespace-nowrap rounded-lg text-sm font-medium transition-all duration-200 ${
              hidden ? 'max-w-0 opacity-0 px-0 py-1.5' : 'max-w-[180px] opacity-100 px-3 py-1.5'
            } ${current ? 'bg-violet-600 text-white' : 'text-zinc-400 hover:text-zinc-100'}`}
          >
            {item.label}
            {item.to === '/chat' && streaming && (
              <span className="absolute top-1 right-1 w-1.5 h-1.5 rounded-full bg-violet-300 animate-pulse" />
            )}
          </Link>
        )
      })}
      {group.items.length > 1 && (
        <span aria-hidden="true"
              className={`text-zinc-600 transition-all duration-200 overflow-hidden ${expanded ? 'max-w-0 opacity-0' : 'max-w-[16px] opacity-100 pr-1.5 pl-0.5'}`}>
          ›
        </span>
      )}
    </div>
  )
}

export function AppShell() {
  const { pathname } = useLocation()
  const [expanded, setExpanded] = useState(null)
  const { streaming } = useChatContext()
  const { tracks } = useLyrics()

  return (
    <div className="h-svh overflow-hidden bg-zinc-950 flex flex-col">
      <DemoBanner />
      <LyricsTape tracks={tracks} />
      <StaleBanner />

      <header className="shrink-0 flex items-center gap-3 px-4 md:px-6 py-2.5 border-b border-zinc-800 bg-zinc-950 overflow-x-auto scrollbar-none">
        <Link to="/dashboard" className="flex items-baseline gap-2 shrink-0 mr-1">
          <span className="text-base">🎸</span>
          <span className="text-sm font-semibold text-zinc-100 tracking-tight">Tastemaker</span>
        </Link>
        <nav className="flex items-center gap-1.5" aria-label="Sections">
          {GROUPS.map(group => (
            <NavGroup
              key={group.id}
              group={group}
              pathname={pathname}
              streaming={streaming}
              expanded={expanded === group.id}
              onExpand={setExpanded}
            />
          ))}
        </nav>
        <Link
          to="/sync"
          title="Sync & pipelines"
          className={`ml-auto shrink-0 px-2.5 py-1.5 rounded-lg text-sm transition-colors ${
            pathname === '/sync' ? 'bg-violet-600 text-white' : 'text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800'
          }`}
        >
          ↻
        </Link>
      </header>

      <div className="flex-1 overflow-hidden">
        <Outlet />
      </div>
    </div>
  )
}
