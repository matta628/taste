import { create } from 'zustand';

export const useUIStore = create((set, get) => ({
    // Current analytics page
    currentPage: 'dashboard',
    setCurrentPage: (page) => set({ currentPage: page }),

    // Dashboard state
    dashboardPeriod: '90d',
    dashboardGenreFilter: null, // null or genre name
    // 'lyrics' sits where the mood donut used to: mood is Claude's guess from
    // lyrics and tags, and a real line from a song in rotation says more.
    // Mood is still one click away in the hidden-charts strip.
    dashboardChartOrder: ['activity', 'genre', 'lyrics', 'top_entities', 'heatmap', 'dow', 'new_artists', 'streak'],
    dashboardHiddenCharts: [],
    activeDashboardId: null,
    setDashboardPeriod: (period) => set({ dashboardPeriod: period }),
    setDashboardGenreFilter: (genre) => set({ dashboardGenreFilter: genre }),
    setDashboardChartOrder: (order) => set({ dashboardChartOrder: order }),
    setActiveDashboardId: (id) => set({ activeDashboardId: id }),
    toggleDashboardChart: (id) =>
        set((s) => ({
            dashboardHiddenCharts: s.dashboardHiddenCharts.includes(id)
                ? s.dashboardHiddenCharts.filter((c) => c !== id)
                : [...s.dashboardHiddenCharts, id],
        })),

    // Deep Dive state
    deepDiveType: null,
    deepDiveId: null,
    deepDivePeriod: '1y',
    deepDiveGranularity: null, // null = auto (derived from period)
    deepDiveMetric: 'plays',
    deepDiveChartType: 'line',
    deepDivePanel: 'Stats',
    deepDiveAnnotations: true,
    deepDiveCompareEntities: [], // [{ type, id }]
    setDeepDive: (type, id) => set({ deepDiveType: type, deepDiveId: id }),
    setDeepDivePeriod: (period) => set({ deepDivePeriod: period }),
    setDeepDiveGranularity: (gran) => set({ deepDiveGranularity: gran }),
    setDeepDiveMetric: (metric) => set({ deepDiveMetric: metric }),
    setDeepDiveChartType: (ct) => set({ deepDiveChartType: ct }),
    setDeepDivePanel: (panel) => set({ deepDivePanel: panel }),
    setDeepDiveAnnotations: (v) => set({ deepDiveAnnotations: v }),
    lastViewedEntity: null,
    setLastViewedEntity: (entity) => set({ lastViewedEntity: entity }),
    addCompareEntity: (entity) =>
        set((s) => ({
            deepDiveCompareEntities: s.deepDiveCompareEntities.find(
                (e) => e.id === entity.id,
            )
                ? s.deepDiveCompareEntities
                : [...s.deepDiveCompareEntities, entity].slice(0, 4),
        })),
    removeCompareEntity: (id) =>
        set((s) => ({
            deepDiveCompareEntities: s.deepDiveCompareEntities.filter(
                (e) => e.id !== id,
            ),
        })),

    // Discover state
    discoverEntity: 'artist',
    discoverSearch: '',
    discoverSortBy: 'rank_all_time',
    discoverSortDir: 'asc',
    discoverTopN: 50,
    discoverVizType: null, // null = no viz; 'bar'|'scatter'|'bubble'|'pie'
    discoverVizAxes: {
        x: 'total_plays',
        y: 'days_since_last_heard',
        size: null,
    },
    discoverColumns: null, // null = default columns
    discoverColumnWidths: {}, // { `${entity}_${colKey}`: px number }
    discoverView: 'table', // 'table'|'cards'|'split'
    discoverFilters: [], // [{ id, field, operator, value }]
    discoverReports: [], // [{ id, name, entity, columns, filters, sort_by, sort_dir }]
    discoverSets: [], // [{ id, name, members: [artist names] }]
    discoverActiveSetId: null, // null or set id
    setDiscoverEntity: (entity) => set({ discoverEntity: entity }),
    setDiscoverReports: (reports) => set({ discoverReports: reports }),
    setDiscoverSets: (sets) => set({ discoverSets: sets }),
    setDiscoverActiveSetId: (setId) => set({ discoverActiveSetId: setId }),
    addDiscoverSet: (name, entityType = 'artist', members = []) =>
        set((s) => ({
            discoverSets: [...s.discoverSets, { id: Date.now(), name, entity_type: entityType, members }],
        })),
    updateDiscoverSet: (setId, updates) =>
        set((s) => ({
            discoverSets: s.discoverSets.map((st) =>
                st.id === setId ? { ...st, ...updates } : st,
            ),
        })),
    removeDiscoverSet: (setId) =>
        set((s) => ({
            discoverSets: s.discoverSets.filter((st) => st.id !== setId),
            discoverActiveSetId: s.discoverActiveSetId === setId ? null : s.discoverActiveSetId,
        })),
    setDiscoverSearch: (search) => set({ discoverSearch: search }),
    setDiscoverSort: (sortBy, sortDir) =>
        set({ discoverSortBy: sortBy, discoverSortDir: sortDir }),
    setDiscoverTopN: (n) => set({ discoverTopN: n }),
    setDiscoverVizType: (type) => set({ discoverVizType: type }),
    setDiscoverVizAxes: (axes) =>
        set((s) => ({ discoverVizAxes: { ...s.discoverVizAxes, ...axes } })),
    setDiscoverColumns: (cols) => set({ discoverColumns: cols }),
    setDiscoverColumnWidth: (key, width) =>
        set((s) => {
            const w = { ...s.discoverColumnWidths }
            if (width == null) delete w[key]
            else w[key] = width
            return { discoverColumnWidths: w }
        }),
    setDiscoverView: (view) => set({ discoverView: view }),
    setDiscoverFilters: (filters) => set({ discoverFilters: filters }),
    addDiscoverFilter: () =>
        set((s) => ({
            discoverFilters: [
                ...s.discoverFilters,
                { id: Date.now(), field: 'total_plays', operator: 'gte', value: '' },
            ],
        })),
    updateDiscoverFilter: (id, updates) =>
        set((s) => ({
            discoverFilters: s.discoverFilters.map((f) =>
                f.id === id ? { ...f, ...updates } : f,
            ),
        })),
    removeDiscoverFilter: (id) =>
        set((s) => ({
            discoverFilters: s.discoverFilters.filter((f) => f.id !== id),
        })),

    // Time Machine state
    timeMachinePreset: '2024',
    timeMachineFrom: '2024-01-01',
    timeMachineTo: '2025-01-01',
    timeMachineCompareMode: 'off', // 'off'|'vs_now'|'vs_era'
    timeMachineCompareFrom: '',
    timeMachineCompareTo: '',
    timeMachineBookmarks: [], // [{ id, name, from, to }]
    setTimeMachineEra: (preset, from, to) =>
        set({
            timeMachinePreset: preset,
            timeMachineFrom: from,
            timeMachineTo: to,
        }),
    setTimeMachineCompareMode: (mode) => set({ timeMachineCompareMode: mode }),
    setTimeMachineCompareEra: (from, to) =>
        set({ timeMachineCompareFrom: from, timeMachineCompareTo: to }),
    addTimeMachineBookmark: (name, from, to) =>
        set((s) => ({
            timeMachineBookmarks: [
                ...s.timeMachineBookmarks,
                { id: Date.now(), name, from, to },
            ],
        })),
    removeTimeMachineBookmark: (id) =>
        set((s) => ({
            timeMachineBookmarks: s.timeMachineBookmarks.filter((b) => b.id !== id),
        })),
    loadTimeMachineBookmark: (id) =>
        set((s) => {
            const bookmark = s.timeMachineBookmarks.find((b) => b.id === id)
            return bookmark
                ? {
                      timeMachinePreset: 'custom',
                      timeMachineFrom: bookmark.from,
                      timeMachineTo: bookmark.to,
                  }
                : {}
        }),

    // Analytics chat panel
    chatPanelOpen: false,
    setChatPanelOpen: (open) => set({ chatPanelOpen: open }),
    toggleChatPanel: () => set((s) => ({ chatPanelOpen: !s.chatPanelOpen })),

    // Action bus highlight — which chart/section is currently spotlit
    highlightedChart: null,
    setHighlightedChart: (id) => set({ highlightedChart: id }),
    clearHighlightedChart: () => set({ highlightedChart: null }),
}));
