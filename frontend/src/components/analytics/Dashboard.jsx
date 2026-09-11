import { useState } from 'react';
import { AnalyticsShell } from './AnalyticsShell';
import { ArtworkStrip } from './ArtworkStrip';
import { useUIStore } from '../../store/uiStore';
import { ActivityChart } from './charts/ActivityChart';
import { GenreChart } from './charts/GenreChart';
import { MoodChart } from './charts/MoodChart';
import { LyricsCarousel, LyricsRail } from './charts/LyricsCarousel';
import { TopEntitiesChart } from './charts/TopEntitiesChart';
import { HeatmapChart } from './charts/HeatmapChart';
import { DayOfWeekChart } from './charts/DayOfWeekChart';
import { NewArtistsChart } from './charts/NewArtistsChart';
import { StreakCalendar } from './charts/StreakCalendar';
import { periodToDates } from './charts/chartTheme';

const PERIODS = [
    { key: '7d', label: '7d' },
    { key: '30d', label: '30d' },
    { key: '90d', label: '90d' },
    { key: '1y', label: '1y' },
    { key: '2y', label: '2y' },
    { key: '3y', label: '3y' },
    { key: '4y', label: '4y' },
    { key: '5y', label: '5y' },
    { key: 'all', label: 'All' },
];

const GRANULARITY = {
    '7d': 'day', '30d': 'day', '90d': 'week', '1y': 'week',
    '2y': 'month', '3y': 'month', '4y': 'month', '5y': 'month', all: 'month',
};

// Charts that occupy one grid column (half-width); all others span both
const HALF_WIDTH = new Set(['genre', 'mood', 'lyrics', 'dow', 'new_artists']);

const CHART_LABELS = {
    activity:    'Activity',
    genre:       'Genre Breakdown',
    mood:        'Mood / Energy',
    lyrics:      'Lyrics',
    top_entities:'Top Artists/Albums/Tracks',
    heatmap:     'Heatmap',
    dow:         'Plays by Day',
    new_artists: 'New Artists',
    streak:      'Streak Calendar',
};

function DraggableSection({ id, dragState, onDragStart, onDragOver, onDrop, onToggleHide, children }) {
    const highlightedChart = useUIStore(s => s.highlightedChart);
    const isOver = dragState.over === id && dragState.dragging !== id;
    const isHighlighted = !isOver && highlightedChart === id;
    return (
        <div
            draggable
            onDragStart={() => onDragStart(id)}
            onDragOver={(e) => { e.preventDefault(); onDragOver(id); }}
            onDrop={() => onDrop(id)}
            onDragEnd={() => onDragStart(null)}
            className={`relative group transition-all duration-500 ${
                HALF_WIDTH.has(id) ? '' : 'col-span-2'
            } ${
                isOver        ? 'ring-2 ring-violet-500 ring-offset-2 ring-offset-zinc-900 rounded-xl' :
                isHighlighted ? 'ring-2 ring-violet-400/70 ring-offset-1 ring-offset-zinc-950 shadow-[0_0_24px_rgba(139,92,246,0.3)] rounded-2xl' : ''
            } ${dragState.dragging === id ? 'opacity-50' : 'opacity-100'}`}
        >
            {/* Drag handle + hide button — visible on hover */}
            <div className="absolute top-2 right-2 z-10 opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1">
                <button
                    onClick={() => onToggleHide(id)}
                    title="Hide chart"
                    className="text-zinc-700 hover:text-zinc-400 text-xs leading-none select-none px-1"
                    onMouseDown={(e) => e.stopPropagation()}
                >
                    ✕
                </button>
                <span className="cursor-grab active:cursor-grabbing text-zinc-700 hover:text-zinc-500 select-none text-lg leading-none">⠿</span>
            </div>
            {children}
        </div>
    );
}

export function Dashboard() {
    const {
        dashboardPeriod: period,
        setDashboardPeriod: setPeriod,
        dashboardGenreFilter,
        setDashboardGenreFilter,
        dashboardChartOrder,
        setDashboardChartOrder,
        dashboardHiddenCharts,
        toggleDashboardChart,
    } = useUIStore();

    const { from_date: fromDate, to_date: toDate } = periodToDates(period);
    const gran = GRANULARITY[period] ?? 'week';

    const [dragState, setDragState] = useState({ dragging: null, over: null });

    const handleDragStart = (id) => setDragState(s => ({ ...s, dragging: id }));
    const handleDragOver  = (id) => setDragState(s => ({ ...s, over: id }));
    const handleDrop = (targetId) => {
        const { dragging } = dragState;
        if (!dragging || dragging === targetId) { setDragState({ dragging: null, over: null }); return; }
        const order = [...dashboardChartOrder];
        const fromIdx = order.indexOf(dragging);
        const toIdx   = order.indexOf(targetId);
        if (fromIdx === -1 || toIdx === -1) return;
        order.splice(fromIdx, 1);
        order.splice(toIdx, 0, dragging);
        setDashboardChartOrder(order);
        setDragState({ dragging: null, over: null });
    };

    const dragProps = (id) => ({
        id, dragState,
        onDragStart: handleDragStart,
        onDragOver:  handleDragOver,
        onDrop:      handleDrop,
        onToggleHide: toggleDashboardChart,
    });

    const renderSlot = (id) => {
        switch (id) {
            case 'activity':
                return <DraggableSection key={id} {...dragProps(id)}><ActivityChart fromDate={fromDate} toDate={toDate} granularity={gran} /></DraggableSection>;
            case 'genre':
                return <DraggableSection key={id} {...dragProps(id)}><GenreChart fromDate={fromDate} toDate={toDate} /></DraggableSection>;
            case 'mood':
                return <DraggableSection key={id} {...dragProps(id)}><MoodChart fromDate={fromDate} toDate={toDate} /></DraggableSection>;
            case 'lyrics':
                return <DraggableSection key={id} {...dragProps(id)}><LyricsCarousel /></DraggableSection>;
            case 'top_entities':
                return <DraggableSection key={id} {...dragProps(id)}><TopEntitiesChart fromDate={fromDate} toDate={toDate} period={period} genreFilter={dashboardGenreFilter} /></DraggableSection>;
            case 'heatmap':
                return <DraggableSection key={id} {...dragProps(id)}><HeatmapChart fromDate={fromDate} toDate={toDate} /></DraggableSection>;
            case 'dow':
                return <DraggableSection key={id} {...dragProps(id)}><DayOfWeekChart fromDate={fromDate} toDate={toDate} /></DraggableSection>;
            case 'new_artists':
                return <DraggableSection key={id} {...dragProps(id)}><NewArtistsChart fromDate={fromDate} toDate={toDate} /></DraggableSection>;
            case 'streak':
                return <DraggableSection key={id} {...dragProps(id)}><StreakCalendar fromDate={fromDate} toDate={toDate} /></DraggableSection>;
            // legacy IDs — keep working if stored state uses old names
            case 'genre_mood':
                return (
                    <DraggableSection key={id} {...dragProps(id)}>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <GenreChart fromDate={fromDate} toDate={toDate} />
                            <MoodChart fromDate={fromDate} toDate={toDate} />
                        </div>
                    </DraggableSection>
                );
            case 'dow_new':
                return (
                    <DraggableSection key={id} {...dragProps(id)}>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <DayOfWeekChart fromDate={fromDate} toDate={toDate} />
                            <NewArtistsChart fromDate={fromDate} toDate={toDate} />
                        </div>
                    </DraggableSection>
                );
            default:
                return null;
        }
    };

    const visible = dashboardChartOrder.filter(id => !dashboardHiddenCharts.includes(id));

    return (
        <AnalyticsShell>
            <div className="h-full overflow-y-auto">
                <div className="px-6 py-5 flex gap-5 justify-center">
                    {/* Lyrics run down the margins where there's room for them. */}
                    <aside className="hidden 2xl:block w-52 shrink-0"><LyricsRail count={3} side="left" /></aside>
                    <div className="flex-1 min-w-0 max-w-7xl">
                    {/* Time range + genre filter */}
                    <div className="flex flex-wrap items-center gap-3 mb-4">
                        <div className="flex items-center gap-1">
                            {PERIODS.map((p) => (
                                <button
                                    key={p.key}
                                    onClick={() => setPeriod(p.key)}
                                    className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                                        period === p.key
                                            ? 'bg-violet-600 text-white'
                                            : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800'
                                    }`}
                                >
                                    {p.label}
                                </button>
                            ))}
                        </div>
                        {dashboardGenreFilter && (
                            <div className="flex items-center gap-2 px-3 py-1.5 bg-violet-900/50 rounded-lg text-xs text-violet-200">
                                <span>Genre: <span className="font-medium">{dashboardGenreFilter}</span></span>
                                <button onClick={() => setDashboardGenreFilter(null)} className="text-violet-300 hover:text-violet-100">✕</button>
                            </div>
                        )}
                    </div>

                    {/* Artwork strip — the visual lead-in above the charts */}
                    <ArtworkStrip period={period} />

                    {/* 2-column grid — half-width charts sit side-by-side, full-width span both */}
                    <div className="grid grid-cols-2 gap-4">
                        {visible.map(renderSlot)}
                    </div>

                    {/* Hidden charts restore strip */}
                    {dashboardHiddenCharts.length > 0 && (
                        <div className="flex flex-wrap gap-2 pt-4 mt-4 border-t border-zinc-800">
                            <span className="text-[10px] text-zinc-600 uppercase tracking-wide self-center">Hidden:</span>
                            {dashboardHiddenCharts.map(id => (
                                <button key={id} onClick={() => toggleDashboardChart(id)}
                                    className="px-2 py-1 rounded bg-zinc-800 text-zinc-500 hover:text-zinc-300 text-xs">
                                    + {CHART_LABELS[id] ?? id}
                                </button>
                            ))}
                        </div>
                    )}
                    </div>
                    <aside className="hidden 2xl:block w-52 shrink-0"><LyricsRail count={3} side="right" /></aside>
                </div>
            </div>
        </AnalyticsShell>
    );
}
