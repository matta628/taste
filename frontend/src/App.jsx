import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import { api } from './api'
import { AppShell } from './components/AppShell'
import { SongLibrary } from './components/SongLibrary'
import { SongForm } from './components/SongForm'
import { PracticeQueue } from './components/PracticeQueue'
import { NudgePanel } from './components/NudgePanel'
import { OldChat } from './components/OldChat'
import { ChatProvider } from './components/ChatContext'
import { PlaylistCreator } from './components/PlaylistCreator'
import { SyncTab } from './components/SyncTab'
import { MoodReview } from './components/MoodReview'
import { BooksPage } from './components/books/BooksPage'
import { Dashboard } from './components/analytics/Dashboard'
import { DeepDive } from './components/analytics/DeepDive'
import { Explore } from './components/analytics/Explore'
import { Discover } from './components/analytics/Discover'
import { TimeMachine } from './components/analytics/TimeMachine'
import './index.css'

// Library, Practice and Add Song all work off the same song list, so they share
// one loader rather than fetching per route.
function GuitarPage({ tab }) {
  const [songs, setSongs] = useState([])
  const [editingSong, setEditingSong] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const navigate = useNavigate()

  const fetchSongs = async () => {
    try {
      setSongs(await api.getSongs())
      setError(null)
    } catch {
      setError('Could not reach the API. Is the backend running?')
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { fetchSongs() }, [])

  const handleAdd = async (form) => { await api.addSong(form); await fetchSongs(); navigate('/guitar') }
  const handleUpdate = async (form) => {
    await api.updateSong(editingSong.song_id, form)
    await fetchSongs()
    setEditingSong(null)
  }
  const handleDelete = async (id) => {
    if (!window.confirm('Remove this song?')) return
    await api.deleteSong(id)
    await fetchSongs()
  }
  const patch = (id, updates) => api.updateSong(id, updates).then(fetchSongs)
  const handleJump = (songId) => {
    const el = document.getElementById(`song-${songId}`)
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.classList.add('highlight-row')
    setTimeout(() => el.classList.remove('highlight-row'), 1800)
  }

  return (
    <div className="h-full overflow-y-auto px-4 md:px-8 py-5">
      {error && (
        <div className="bg-red-950 border border-red-800 text-red-300 text-sm rounded-xl px-4 py-3 mb-4">{error}</div>
      )}
      {loading ? (
        <p className="text-zinc-500 text-center pt-16">Loading…</p>
      ) : editingSong ? (
        <div className="max-w-lg">
          <SongForm
            initial={editingSong}
            onSave={handleUpdate}
            onCancel={() => setEditingSong(null)}
            songs={songs}
          />
        </div>
      ) : tab === 'practice' ? (
        <PracticeQueue songs={songs} onRefresh={fetchSongs} />
      ) : tab === 'add' ? (
        <div className="max-w-lg"><SongForm onSave={handleAdd} songs={songs} /></div>
      ) : (
        <>
          <NudgePanel songs={songs} onSave={(id, field, value) => patch(id, { [field]: value })} onJump={handleJump} />
          <SongLibrary
            songs={songs}
            onEdit={setEditingSong}
            onDelete={handleDelete}
            onUpdateDifficulty={(id, difficulty) => patch(id, { difficulty })}
            onUpdateNotes={(id, notes) => patch(id, { notes })}
            onUpdateDate={(id, date_started) => patch(id, { date_started })}
          />
        </>
      )}
    </div>
  )
}

function ChatPage() {
  const navigate = useNavigate()
  return (
    <div className="h-full px-4 md:px-8">
      <OldChat onGoToPlaylist={() => navigate('/playlist')} />
    </div>
  )
}

const scroller = (children) => <div className="h-full overflow-y-auto px-4 md:px-8 py-5">{children}</div>

export default function App() {
  return (
    <BrowserRouter basename={import.meta.env.VITE_BASE_PATH || '/'}>
      <ChatProvider>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/explore" element={<Explore />} />
            <Route path="/explore/:type/:id" element={<DeepDive />} />
            <Route path="/discover" element={<Discover />} />
            <Route path="/timemachine" element={<TimeMachine />} />
            <Route path="/mood" element={scroller(<MoodReview />)} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/playlist" element={<div className="h-full px-4 md:px-8"><PlaylistCreator /></div>} />
            <Route path="/books" element={<BooksPage />} />
            <Route path="/guitar" element={<GuitarPage tab="library" />} />
            <Route path="/guitar/practice" element={<GuitarPage tab="practice" />} />
            <Route path="/guitar/add" element={<GuitarPage tab="add" />} />
            <Route path="/sync" element={scroller(<SyncTab />)} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Route>
        </Routes>
      </ChatProvider>
    </BrowserRouter>
  )
}
