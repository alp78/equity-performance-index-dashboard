<!--
  SectorPanel — Sector Rotation Mode Panel
  Extracted from Sidebar.svelte.  Handles sector rotation mode:

  - Cross-Index / Single-Index toggle
  - Cross-index: index checkboxes, sector tree with industry filtering
  - Single-index: index dropdown, sector tree with industry checkboxes
  - Session-persisted state for selected indices, sectors, industries

  Props:
    onGoToStockMode(sectorName) — callback to switch to stock browsing mode

  All sector state comes from $lib/stores.js reactive stores.
-->

<script>
    import { onMount, tick, untrack } from 'svelte';
    import { browser } from '$app/environment';
    import {
        INDEX_CONFIG,
        selectedSector,
        sectorAnalysisMode,
        selectedIndustries,
        crossSelectedIndustries,
        singleModeIndustries,
        selectedSectors,
        sectorSelectedIndices,
        singleSelectedIndex,
        sectorHighlightEnabled,
    } from '$lib/stores.js';
    import { getSectorColor, INDEX_COLORS } from '$lib/theme.js';
    import { API_BASE_URL } from '$lib/config.js';

    // --- PROPS ---

    let { onGoToStockMode } = $props();

    // --- SECTOR ROTATION STATE ---

    let availableSectors = $state([]);
    let sectorsLoaded = $state(false);
    let sectorIndustries = $state([]);
    let _lastLoadedSector = '';
    let _lastEffectSector = '';
    let _lastEffectMode = '';
    let _crossSector = $selectedSector || 'Materials';
    let _singleSector = $selectedSector || 'Materials';
    let _sectorDefaultNeeded = browser ? !sessionStorage.getItem('dash_sector_visited') : true;
    let sectorPanelOpen = $state(new Set());
    let allSectorIndustries = $state({});
    let singleIndexIndustries = $state({});
    let singleOpenIndex = $state((() => {
        if (!browser) return 'stoxx50';
        try { return sessionStorage.getItem('dash_single_open_index') || 'stoxx50'; } catch { return 'stoxx50'; }
    })());
    let singleOpenSectors = $state(new Set());
    let singleDropdownOpen = $state(false);
    let singleIndexSectors = $state({});
    let singleSelectedIndustries = $state({});

    // --- DERIVED STOCK COUNTS ---

    let sectorStockCount = $derived((() => {
        if (!sectorIndustries.length) return 0;
        const included = sectorIndustries.filter(ind =>
            $selectedIndustries.length === 0 || $selectedIndustries.includes(ind.industry)
        );
        return included.reduce((sum, ind) => sum + (ind.total || 0), 0);
    })());

    let indexStockCounts = $derived((() => {
        const counts = {};
        if (!sectorIndustries.length) return counts;
        const included = sectorIndustries.filter(ind =>
            $selectedIndustries.length === 0 || $selectedIndustries.includes(ind.industry)
        );
        for (const ind of included) {
            for (const [idx, cnt] of Object.entries(ind.indices || {})) {
                counts[idx] = (counts[idx] || 0) + cnt;
            }
        }
        return counts;
    })());

    // --- AUTO-EXPAND HIGHLIGHTED SECTOR (SINGLE-INDEX) ---

    $effect(() => {
        const sec = $selectedSector;
        if (!sec || $sectorAnalysisMode !== 'single-index') return;
        if (!singleOpenSectors.has(sec)) {
            const next = new Set(singleOpenSectors);
            next.add(sec);
            singleOpenSectors = next;
            const idxKey = singleOpenIndex;
            const ck = `${sec}_${idxKey}`;
            if (idxKey && !allSectorIndustries[ck]) {
                fetch(`${API_BASE_URL}/sector-comparison/industries?sector=${encodeURIComponent(sec)}&indices=${idxKey}`)
                        .then(r => r.ok ? r.json() : [])
                        .then(data => { allSectorIndustries[ck] = data; singleIndexIndustries = { ...singleIndexIndustries, [ck]: data }; });
            } else if (idxKey && allSectorIndustries[ck]) {
                singleIndexIndustries = { ...singleIndexIndustries, [ck]: allSectorIndustries[ck] };
            }
        }
    });

    // --- INDEX TOGGLE (CROSS-INDEX) ---

    function toggleSectorIndex(key) {
        sectorSelectedIndices.update(list => {
            if (list.includes(key)) {
                return list.length > 1 ? list.filter(k => k !== key) : list;
            }
            return [...list, key];
        });
        const newIndicesStr = [...$sectorSelectedIndices].sort().join(',');
        _industriesPreloaded = false;
        preloadAllIndustries(newIndicesStr);
        if ($selectedSector) {
            loadIndustries($selectedSector);
        }
    }

    // --- SINGLE-INDEX DATA LOADING ---

    async function _loadSingleIndexData(key) {
        let sectors = singleIndexSectors[key];
        if (!sectors) {
            try {
                                const res = await fetch(`${API_BASE_URL}/sector-comparison/sectors?indices=${key}`);
                if (res.ok) {
                    sectors = await res.json();
                    singleIndexSectors = { ...singleIndexSectors, [key]: sectors };
                }
            } catch {}
        }
        if (sectors && sectors.length > 0) {
            for (const sec of sectors) {
                const ck = `${sec}_${key}`;
                if (allSectorIndustries[ck]) {
                    singleIndexIndustries = { ...singleIndexIndustries, [ck]: allSectorIndustries[ck] };
                } else {
                    try {
                                                const res = await fetch(`${API_BASE_URL}/sector-comparison/industries?sector=${encodeURIComponent(sec)}&indices=${key}`);
                        if (res.ok) {
                            const data = await res.json();
                            allSectorIndustries[ck] = data;
                            singleIndexIndustries = { ...singleIndexIndustries, [ck]: data };
                        }
                    } catch {}
                }
            }
        }
    }

    async function openSingleIndex(key) {
        if (singleOpenIndex === key) return;
        singleOpenIndex = key;
        const activeSec = $selectedSector;
        singleOpenSectors = activeSec ? new Set([activeSec]) : new Set();
        singleSelectedIndex.set([key]);
        try { sessionStorage.setItem('dash_single_open_index', key); } catch {}
        await _loadSingleIndexData(key);
    }

    // --- SECTOR/INDUSTRY TOGGLES ---

    function toggleCrossIndustry(sector, industry, allInds, exclusive = false) {
        const current = $crossSelectedIndustries[sector] || [];
        let next;
        if (exclusive) {
            next = (current.length === 1 && current[0] === industry) ? [] : [industry];
        } else if (current.length === 0) {
            next = allInds.filter(i => i !== industry);
        } else if (current.includes(industry)) {
            const filtered = current.filter(i => i !== industry);
            next = filtered.length === 0 ? [] : filtered;
        } else {
            const updated = [...current, industry];
            next = updated.length >= allInds.length ? [] : updated;
        }
        crossSelectedIndustries.update(m => ({ ...m, [sector]: next }));
        if (sector === $selectedSector) {
            selectedIndustries.set(next);
        }
    }

    function clearCrossIndustries(sector) {
        crossSelectedIndustries.update(m => ({ ...m, [sector]: [] }));
        if (sector === $selectedSector) selectedIndustries.set([]);
    }

    function toggleSingleIndustry(sector, industry, allIndustries, exclusive = false) {
        const current = singleSelectedIndustries[sector] || [];
        if (exclusive) {
            if (current.length === 1 && current[0] === industry) {
                singleSelectedIndustries = { ...singleSelectedIndustries, [sector]: [] };
            } else {
                singleSelectedIndustries = { ...singleSelectedIndustries, [sector]: [industry] };
            }
            return;
        }
        if (current.length === 0) {
            singleSelectedIndustries = { ...singleSelectedIndustries, [sector]: allIndustries.filter(i => i !== industry) };
        } else if (current.includes(industry)) {
            const newList = current.filter(i => i !== industry);
            singleSelectedIndustries = { ...singleSelectedIndustries, [sector]: newList.length === 0 ? [] : newList };
        } else {
            const newList = [...current, industry];
            singleSelectedIndustries = { ...singleSelectedIndustries, [sector]: newList.length >= allIndustries.length ? [] : newList };
        }
    }

    function clearSingleIndustries(sector) {
        singleSelectedIndustries = { ...singleSelectedIndustries, [sector]: [] };
    }

    function selectAllForIndex(key) {
        const sectors = singleIndexSectors[key] || availableSectors || [];
        selectedSectors.set([...sectors]);
        singleSelectedIndustries = {};
    }

    function toggleMultiSector(sec) {
        selectedSectors.update(list => {
            if (list.includes(sec)) {
                if (list.length <= 1) return list;
                if ($selectedSector === sec) selectedSector.set(null);
                return list.filter(s => s !== sec);
            }
            return [...list, sec];
        });
    }

    // --- CROSS-INDEX INDUSTRY LOADING ---

    function getBestCachedIndustries(sector) {
        const prefix = sector + '_';
        for (const key of Object.keys(allSectorIndustries)) {
            if (key.startsWith(prefix) && allSectorIndustries[key].length > 0) return allSectorIndustries[key];
        }
        return null;
    }

    async function loadIndustries(sector) {
        if (!sector) { sectorIndustries = []; return; }
        const indicesStr = [...$sectorSelectedIndices].sort().join(',') || Object.keys(INDEX_CONFIG).sort().join(',');
        const cacheKey = `${sector}_${indicesStr}`;
        if (allSectorIndustries[cacheKey]) {
            sectorIndustries = allSectorIndustries[cacheKey];
            return;
        }
        _lastLoadedSector = sector;
        try {
                        const res = await fetch(`${API_BASE_URL}/sector-comparison/industries?sector=${encodeURIComponent(sector)}&indices=${indicesStr}`);
            if (res.ok) {
                const data = await res.json();
                sectorIndustries = data;
                allSectorIndustries = { ...allSectorIndustries, [cacheKey]: data };
            }
        } catch { sectorIndustries = []; }
    }

    let _industriesPreloaded = false;
    async function preloadAllIndustries(indicesStr) {
        if (_industriesPreloaded) return;
        _industriesPreloaded = true;
        try {
                        const res = await fetch(`${API_BASE_URL}/sector-comparison/all-industries?indices=${indicesStr}`);
            if (res.ok) {
                const data = await res.json();
                const updates = {};
                for (const [sec, industries] of Object.entries(data)) {
                    const ck = `${sec}_${indicesStr}`;
                    updates[ck] = industries;
                }
                allSectorIndustries = { ...allSectorIndustries, ...updates };
                const currentSec = $selectedSector;
                const currentKey = `${currentSec}_${indicesStr}`;
                if (updates[currentKey]) {
                    sectorIndustries = updates[currentKey];
                }
            }
        } catch { _industriesPreloaded = false; }
    }

    function toggleSectorPanel(sec) {
        if (sectorPanelOpen.has(sec)) {
            return;
        } else {
            selectedSector.set(sec);
            sectorPanelOpen = new Set([sec]);
            loadIndustries(sec);
            const perSectorFilter = $crossSelectedIndustries[sec] || [];
            if (JSON.stringify(perSectorFilter) !== JSON.stringify($selectedIndustries)) {
                selectedIndustries.set(perSectorFilter);
            }
        }
    }

    // --- REACTIVE: SECTOR MODE INITIALIZATION ---

    $effect(() => {
        if (!sectorsLoaded) {
            const allKeys = Object.keys(INDEX_CONFIG).join(',');
                fetch(`${API_BASE_URL}/sector-comparison/sectors?indices=${allKeys}`)
                    .then(r => r.ok ? r.json() : [])
                    .then(data => {
                        availableSectors = data;
                        sectorsLoaded = true;
                        if (data.length > 0 && _sectorDefaultNeeded) {
                            _sectorDefaultNeeded = false;
                            try { sessionStorage.setItem('dash_sector_visited', '1'); } catch {}
                            const first = data[0];
                            selectedSector.set(first);
                            _crossSector = first;
                            _singleSector = first;
                            _lastEffectSector = '';
                        }
                        preloadAllIndustries(allKeys);
                    })
                    .catch(() => { sectorsLoaded = true; });
        }
        if ($sectorAnalysisMode !== _lastEffectMode) {
            _lastEffectMode = $sectorAnalysisMode;
            _lastEffectSector = '';
            if ($sectorAnalysisMode === 'single-index' && singleOpenIndex) {
                _loadSingleIndexData(singleOpenIndex);
            }
        }
        if ($selectedSector && $sectorAnalysisMode === 'cross-index') {
            _crossSector = $selectedSector;
            const sectorChanged = $selectedSector !== _lastEffectSector;
            if (sectorChanged) {
                _lastEffectSector = $selectedSector;
                sectorPanelOpen = new Set([$selectedSector]);
                const perSectorFilter = $crossSelectedIndustries[$selectedSector] || [];
                const currentInds = untrack(() => $selectedIndustries);
                if (JSON.stringify(perSectorFilter) !== JSON.stringify(currentInds)) {
                    selectedIndustries.set(perSectorFilter);
                }
                loadIndustries($selectedSector);
                tick().then(() => {
                    const el = document.querySelector(`[data-sector-cross="${CSS.escape($selectedSector)}"]`);
                    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                });
            }
        }
        if ($selectedSector && $sectorAnalysisMode === 'single-index') {
            _singleSector = $selectedSector;
            const sectorChanged = $selectedSector !== _lastEffectSector;
            if (sectorChanged) {
                _lastEffectSector = $selectedSector;
                const sec = $selectedSector;
                if (!singleOpenSectors.has(sec)) {
                    const next = new Set(singleOpenSectors);
                    next.add(sec);
                    singleOpenSectors = next;
                }
                const idxKey = singleOpenIndex;
                const ck = `${sec}_${idxKey}`;
                if (!allSectorIndustries[ck]) {
                    fetch(`${API_BASE_URL}/sector-comparison/industries?sector=${encodeURIComponent(sec)}&indices=${idxKey}`)
                            .then(r => r.ok ? r.json() : [])
                            .then(data => { allSectorIndustries[ck] = data; singleIndexIndustries = { ...singleIndexIndustries, [ck]: data }; });
                } else {
                    singleIndexIndustries = { ...singleIndexIndustries, [ck]: allSectorIndustries[ck] };
                }
                tick().then(() => {
                    const el = document.querySelector(`[data-sector-single="${CSS.escape(sec)}"]`);
                    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                });
            }
        }
    });

    // --- REACTIVE: SYNC SINGLE-INDEX INDUSTRY FILTERS ---

    $effect(() => {
        if ($sectorAnalysisMode !== 'single-index') return;
        const perSector = singleSelectedIndustries;
        singleModeIndustries.set({ ...perSector });
        let anyFiltered = false;
        for (const [sec, inds] of Object.entries(perSector)) {
            if (inds.length > 0) { anyFiltered = true; break; }
        }
        if (!anyFiltered) {
            if ($selectedIndustries.length > 0) selectedIndustries.set([]);
            return;
        }
        const combined = new Set();
        const activeKey = singleOpenIndex;
        const activeSectors = singleIndexSectors[activeKey] || [];
        for (const sec of activeSectors) {
            if (!$selectedSectors.includes(sec)) continue;
            const secFilter = perSector[sec] || [];
            const allInds = singleIndexIndustries[`${sec}_${activeKey}`] || [];
            if (secFilter.length > 0) {
                for (const name of secFilter) combined.add(name);
            } else {
                for (const ind of allInds) combined.add(ind.industry);
            }
        }
        selectedIndustries.set(Array.from(combined));
    });

    // --- CLICK OUTSIDE ---

    function handleClickOutside(e) {
        if (singleDropdownOpen && !e.target.closest('.single-index-dropdown')) singleDropdownOpen = false;
    }

    // --- LIFECYCLE ---

    onMount(() => {
        document.addEventListener('click', handleClickOutside);
        if ($sectorAnalysisMode === 'single-index' && singleOpenIndex) {
            singleSelectedIndex.set([singleOpenIndex]);
            _loadSingleIndexData(singleOpenIndex);
        }
        return () => document.removeEventListener('click', handleClickOutside);
    });
</script>

<div class="flex flex-col h-full overflow-y-auto overflow-x-hidden custom-scrollbar">
    <div class="p-4 pb-2 space-y-3 shrink-0">
        <!-- mode toggle: cross-index vs single-index -->
        <div class="flex bg-bg-card border border-border rounded-lg p-1 gap-1">
            <button onclick={() => { _singleSector = $selectedSector; selectedSector.set(_crossSector); sectorAnalysisMode.set('cross-index'); }}
                class="flex-1 text-center py-1.5 rounded-md text-[13px] font-semibold uppercase tracking-wider transition-all
                {$sectorAnalysisMode === 'cross-index' ? 'bg-orange-500/20 text-orange-400 border border-orange-500/30' : 'text-text-muted hover:text-text-secondary'}">
                Cross-Index
            </button>
            <button onclick={() => { _crossSector = $selectedSector; selectedSector.set(_singleSector); sectorAnalysisMode.set('single-index'); }}
                class="flex-1 text-center py-1.5 rounded-md text-[13px] font-semibold uppercase tracking-wider transition-all
                {$sectorAnalysisMode === 'single-index' ? 'bg-orange-500/20 text-orange-400 border border-orange-500/30' : 'text-text-muted hover:text-text-secondary'}">
                Single-Index
            </button>
        </div>

        {#if $sectorAnalysisMode === 'cross-index'}
            <!-- index checkboxes (cross-index mode) -->
            <div class="space-y-0.5">
                <div class="flex items-center justify-between mb-1">
                    <span class="text-[12px] font-semibold text-text-muted uppercase tracking-widest">Select Indices</span>
                    {#if sectorStockCount > 0}
                        <span class="text-[13px] font-medium text-text-faint tabular-nums">{sectorStockCount}</span>
                    {/if}
                </div>
                {#each Object.entries(INDEX_CONFIG) as [key, cfg]}
                    {@const isSelected = $sectorSelectedIndices.includes(key)}
                    {@const idxCount = indexStockCounts[key] || 0}
                    <button
                        onclick={() => toggleSectorIndex(key)}
                        class="w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-all
                        {isSelected ? '' : 'bg-transparent hover:bg-bg-hover'}"
                    >
                        <div class="w-3.5 h-3.5 rounded-sm border flex items-center justify-center shrink-0 transition-all
                            {isSelected ? 'border-text-faint bg-bg-active' : 'border-border bg-transparent'}">
                            {#if isSelected}
                                <svg class="w-2.5 h-2.5 text-text-secondary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
                                </svg>
                            {/if}
                        </div>
                        <span class="{INDEX_CONFIG[key]?.flag || ''} fis rounded-sm" style="font-size: 1.1rem;"></span>
                        <span class="text-[15px] font-medium transition-colors"
                              style="color:{isSelected ? (INDEX_COLORS[key] || 'var(--text-secondary)') : 'var(--text-muted)'}">{cfg.shortLabel}</span>
                        {#if isSelected && idxCount > 0}
                            <span class="ml-auto text-[13px] font-medium text-text-faint tabular-nums">{idxCount}</span>
                        {/if}
                    </button>
                {/each}
            </div>
        {/if}
    </div>

    {#if $sectorAnalysisMode === 'cross-index'}
        <!-- cross-index sector tree -->
        <div class="flex-1 overflow-y-auto overflow-x-hidden custom-scrollbar">
            {#each (availableSectors.length > 0 ? availableSectors : ['Communication Services', 'Consumer Discretionary', 'Consumer Staples', 'Energy', 'Financials', 'Healthcare', 'Industrials', 'Information Technology', 'Materials', 'Real Estate', 'Utilities']) as sec}
                {@const isOpen = sectorPanelOpen.has(sec)}
                {@const isActive = $selectedSector === sec}
                {@const isCurrent = isOpen && isActive}
                <button data-sector-cross={sec}
                    onclick={() => toggleSectorPanel(sec)}
                    class="w-full flex items-center justify-between px-4 py-2.5 border-b border-border transition-all relative overflow-hidden
                    {isActive ? 'bg-surface-2 sticky top-0 z-10' : 'bg-surface-1 hover:bg-surface-2 border-l-[3px] border-l-transparent'}"
                >
                    {#if isActive}
                        <div class="absolute left-0 top-0 bottom-0 w-[3px]" style="background: {getSectorColor(sec)}"></div>
                    {/if}
                    <div class="flex items-center gap-2.5">
                        <svg class="w-3 h-3 transition-transform {isOpen ? 'rotate-90' : ''}" style="color: {isActive ? getSectorColor(sec) : 'var(--text-faint)'}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7" />
                        </svg>
                        <span class="text-[14px] font-bold uppercase tracking-wider" style="color: {isActive ? getSectorColor(sec) : 'var(--text-secondary)'}">{sec}</span>
                    </div>
                    <div class="flex items-center gap-2">
                        {#if isCurrent}
                            <span class="text-[13px] font-medium text-text-faint tabular-nums">{sectorStockCount}</span>
                        {/if}
                    </div>
                </button>

                {#if isOpen}
                    {@const indicesStr = [...$sectorSelectedIndices].sort().join(',') || Object.keys(INDEX_CONFIG).sort().join(',')}
                    {@const cacheKey = `${sec}_${indicesStr}`}
                    {@const exactHit = allSectorIndustries[cacheKey]}
                    {@const secInds = exactHit || getBestCachedIndustries(sec) || []}
                    {#if exactHit && secInds.length === 0}
                        <div class="px-4 py-3 ml-2 border-l border-border-subtle">
                            <span class="text-[12px] font-medium text-text-faint uppercase tracking-widest">No data for selected indices</span>
                        </div>
                    {:else if secInds.length > 0}
                        {@const allIndNames = secInds.map(i => i.industry)}
                        {@const secFilter = $crossSelectedIndustries[sec] || []}
                        <div class="flex items-center justify-between px-4 py-[6px] ml-2 border-l border-border-subtle">
                            <span class="text-[11px] font-semibold text-text-faint uppercase tracking-widest">
                                {secFilter.length === 0 ? `${secInds.length} industries` : `${secFilter.length} of ${secInds.length}`}
                            </span>
                            {#if secFilter.length > 0}
                                <button onclick={() => clearCrossIndustries(sec)}
                                    class="text-[11px] font-medium uppercase tracking-wider text-text-muted hover:text-text-secondary transition-all">
                                    Select All
                                </button>
                            {:else}
                                <span class="text-[11px] font-medium text-text-faint uppercase tracking-wider">Ctrl+click to isolate</span>
                            {/if}
                        </div>
                        {#each secInds as ind (ind.industry)}
                            {@const isChecked = secFilter.length === 0 || secFilter.includes(ind.industry)}
                            <button onclick={(e) => toggleCrossIndustry(sec, ind.industry, allIndNames, e.ctrlKey || e.metaKey)}
                                class="w-full flex items-center gap-2.5 px-4 py-[7px] ml-2 border-l transition-all
                                {isChecked ? 'border-l-border' : 'border-l-border-subtle'}"
                                title="Click to toggle, Ctrl+click to select only this one"
                            >
                                <div class="w-3.5 h-3.5 rounded-sm border flex items-center justify-center shrink-0
                                    {isChecked ? 'border-text-faint bg-bg-active' : 'border-border bg-transparent'}">
                                    {#if isChecked}
                                        <svg class="w-2.5 h-2.5 text-text-secondary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
                                        </svg>
                                    {/if}
                                </div>
                                <span class="text-[12px] font-medium uppercase tracking-wide truncate {isChecked ? 'text-text-muted' : 'text-text-faint'}">{ind.industry}</span>
                                <span class="ml-auto text-[12px] text-text-faint tabular-nums font-medium shrink-0">{ind.total}</span>
                            </button>
                        {/each}
                    {:else}
                        <div class="px-6 py-2 ml-2 border-l border-border-subtle">
                            <div class="w-3 h-3 border border-border border-t-text-muted rounded-full animate-spin"></div>
                        </div>
                    {/if}
                {/if}
            {/each}
        </div>
    {:else}
        <!-- single-index: dropdown + sector > industry tree -->
        {@const singleKey = singleOpenIndex}
        {@const singleCfg = INDEX_CONFIG[singleKey] || {}}

        <!-- index dropdown -->
        <div class="relative single-index-dropdown">
            <button onclick={() => { singleDropdownOpen = !singleDropdownOpen; }}
                class="w-full flex items-center justify-between px-4 py-2.5 bg-bg-card border-b border-border-subtle hover:bg-bg-hover transition-all"
            >
                <div class="flex items-center gap-3">
                    <span class="{INDEX_CONFIG[singleKey]?.flag || ''} fis rounded-sm" style="font-size: 1.2rem;"></span>
                    <span class="text-[15px] font-semibold uppercase tracking-widest text-text">{singleCfg.shortLabel || singleKey}</span>
                </div>
                <svg class="w-4 h-4 text-text-faint transition-transform {singleDropdownOpen ? 'rotate-180' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
            </button>
            {#if singleDropdownOpen}
                <div class="absolute left-0 right-0 top-full z-50 bg-bg-card border border-border rounded-b-lg shadow-md overflow-hidden">
                    {#each Object.entries(INDEX_CONFIG) as [key, cfg]}
                        {#if key !== singleKey}
                            <button onclick={() => { openSingleIndex(key); singleDropdownOpen = false; }}
                                class="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-bg-hover transition-all"
                            >
                                <span class="{INDEX_CONFIG[key]?.flag || ''} fis rounded-sm" style="font-size: 1.1rem;"></span>
                                <span class="text-[14px] font-medium uppercase tracking-widest text-text-secondary">{cfg.shortLabel}</span>
                            </button>
                        {/if}
                    {/each}
                </div>
            {/if}
        </div>

        <!-- sector > industry tree for selected index -->
        <div class="flex-1 overflow-y-auto overflow-x-hidden custom-scrollbar">
          {#each [singleKey] as _idxKey}
            {@const sectors = singleIndexSectors[_idxKey] || availableSectors || []}
            {@const hasAnyIndustryFilter = sectors.some(s => (singleSelectedIndustries[s] || []).length > 0)}
            <div class="flex items-center justify-between px-4 py-[6px] border-l border-border-subtle">
                <span class="text-[11px] font-semibold text-text-faint uppercase tracking-widest">
                    {$selectedSectors.length} of {sectors.length} sectors
                </span>
                {#if $selectedSectors.length < sectors.length || hasAnyIndustryFilter}
                    <button onclick={() => selectAllForIndex(singleKey)}
                        class="text-[11px] font-medium uppercase tracking-wider text-text-muted hover:text-text-secondary transition-all">
                        Select All
                    </button>
                {:else}
                    <span class="text-[11px] font-medium text-text-faint uppercase tracking-wider">Ctrl+click to isolate</span>
                {/if}
            </div>
            {#each sectors as sec, sIdx}
                {@const isSectorOpen = singleOpenSectors.has(sec)}
                {@const isSectorActive = $selectedSectors.includes(sec)}
                {@const isSelectedSec = $selectedSector === sec}
                {@const secStockCount = (() => {
                    const inds = singleIndexIndustries[`${sec}_${singleKey}`] || [];
                    const sf = singleSelectedIndustries[sec] || [];
                    let t = 0;
                    for (const ind of inds) {
                        if (sf.length === 0 || sf.includes(ind.industry)) t += ind.total || 0;
                    }
                    return t;
                })()}
                {@const secIndustries = singleIndexIndustries[`${sec}_${singleKey}`] || []}
                <div data-sector-single={sec}
                     onclick={() => { if ($selectedSector === sec) { sectorHighlightEnabled.update(v => !v); return; } if (!$selectedSectors.includes(sec)) { selectedSectors.update(s => [...s, sec]); } sectorHighlightEnabled.set(true); selectedSector.set(sec); }}
                     onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if ($selectedSector === sec) { sectorHighlightEnabled.update(v => !v); return; } if (!$selectedSectors.includes(sec)) { selectedSectors.update(s => [...s, sec]); } sectorHighlightEnabled.set(true); selectedSector.set(sec); } }}
                     role="button" tabindex="0"
                     aria-label="Select sector {sec}"
                     class="flex items-center border-b border-border transition-all border-l-[3px] relative cursor-pointer
                    {isSelectedSec ? 'bg-surface-2 border-l-selected-border' : 'bg-surface-1 hover:bg-surface-2 border-l-transparent'}">
                    {#if isSelectedSec}
                        <div class="absolute left-0 top-0 bottom-0 w-[3px] bg-selected-border"></div>
                    {/if}
                    <button onclick={(e) => {
                            e.stopPropagation();
                            const allSecs = singleIndexSectors[singleOpenIndex] || availableSectors || [];
                            if (e.ctrlKey || e.metaKey) {
                                if ($selectedSectors.length === 1 && $selectedSectors[0] === sec) {
                                    selectedSectors.set([...allSecs]);
                                } else {
                                    selectedSectors.set([sec]);
                                }
                            } else {
                                toggleMultiSector(sec);
                            }
                        }}
                        class="pl-3 pr-2.5 py-2.5 shrink-0 flex items-center justify-center"
                        title="Click to toggle, Ctrl+click to select only this one"
                    >
                        <div class="w-3.5 h-3.5 rounded-sm border flex items-center justify-center transition-all
                            {isSectorActive ? 'border-text-faint bg-bg-active' : 'border-border bg-transparent'}">
                            {#if isSectorActive}
                                <svg class="w-2.5 h-2.5 text-text-secondary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
                                </svg>
                            {/if}
                        </div>
                    </button>
                    <button onclick={(e) => {
                            e.stopPropagation();
                            if (singleOpenSectors.has(sec) && sec === $selectedSector) return;
                            const next = new Set(singleOpenSectors);
                            if (next.has(sec)) { next.delete(sec); } else {
                                next.add(sec);
                                const idxKey = singleOpenIndex;
                                const ck = `${sec}_${idxKey}`;
                                if (!allSectorIndustries[ck]) {
                                    fetch(`${API_BASE_URL}/sector-comparison/industries?sector=${encodeURIComponent(sec)}&indices=${idxKey}`)
                                            .then(r => r.ok ? r.json() : [])
                                            .then(data => { allSectorIndustries[ck] = data; singleIndexIndustries = { ...singleIndexIndustries, [ck]: data }; });
                                } else {
                                    singleIndexIndustries = { ...singleIndexIndustries, [ck]: allSectorIndustries[ck] };
                                }
                            }
                            singleOpenSectors = next;
                        }}
                        class="py-2.5 px-1 shrink-0 flex items-center justify-center"
                        title="Expand {sec}"
                    >
                        <svg class="w-3.5 h-3.5 text-text-faint transition-transform shrink-0 {isSectorOpen ? 'rotate-90' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7" />
                        </svg>
                    </button>
                    <div class="flex-1 flex items-center py-2.5 pr-4 pl-1 min-w-0 group/singlesec">
                        <span class="text-[14px] font-bold uppercase tracking-wider truncate"
                              style="color:{isSelectedSec ? getSectorColor(sec) : isSectorActive ? 'var(--text-secondary)' : 'var(--text-muted)'}">
                            {sec}
                        </span>
                        <div class="ml-auto flex items-center gap-3 shrink-0">
                            <button onclick={(e) => { e.stopPropagation(); onGoToStockMode(sec); }}
                                class="opacity-0 group-hover/singlesec:opacity-100 transition-opacity text-[12px] font-semibold text-text-muted uppercase tracking-wider hover:text-text cursor-pointer whitespace-nowrap">
                                Stock Mode
                            </button>
                            {#if secStockCount > 0}
                                <span class="text-[13px] font-medium text-text-faint tabular-nums w-6 text-right">{secStockCount}</span>
                            {/if}
                        </div>
                    </div>
                </div>

                {#if isSectorOpen}
                    {#if secIndustries.length > 0}
                        {@const secSelInds = singleSelectedIndustries[sec] || []}
                        {@const allIndNames = secIndustries.map(i => i.industry)}
                        <div class="flex items-center justify-between px-4 py-[5px] ml-2 border-l border-border-subtle">
                            <span class="text-[11px] font-semibold text-text-faint uppercase tracking-widest">
                                {secSelInds.length === 0 ? `${secIndustries.length} industries` : `${secSelInds.length} of ${secIndustries.length}`}
                            </span>
                            {#if secSelInds.length > 0}
                                <button onclick={() => clearSingleIndustries(sec)}
                                    class="text-[11px] font-medium uppercase tracking-wider text-text-muted hover:text-text-secondary transition-all">
                                    Select All
                                </button>
                            {:else}
                                <span class="text-[11px] font-medium text-text-faint uppercase tracking-wider">Ctrl+click to isolate</span>
                            {/if}
                        </div>
                        {#each secIndustries as ind}
                            {@const isIndChecked = secSelInds.length === 0 || secSelInds.includes(ind.industry)}
                            <button
                                onclick={(e) => toggleSingleIndustry(sec, ind.industry, allIndNames, e.ctrlKey || e.metaKey)}
                                class="w-full flex items-center gap-2.5 px-4 py-[6px] ml-2 border-l transition-all
                                {isIndChecked ? 'border-l-border' : 'border-l-border-subtle'}"
                                title="Click to toggle, Ctrl+click to select only this one"
                            >
                                <div class="w-3 h-3 rounded-sm border flex items-center justify-center shrink-0
                                    {isIndChecked ? 'border-text-faint bg-bg-active' : 'border-border bg-transparent'}">
                                    {#if isIndChecked}
                                        <svg class="w-2 h-2 text-text-secondary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7" />
                                        </svg>
                                    {/if}
                                </div>
                                <span class="text-[12px] font-medium uppercase tracking-wide truncate {isIndChecked ? 'text-text-muted' : 'text-text-faint'}">{ind.industry}</span>
                                <span class="ml-auto text-[12px] text-text-faint tabular-nums font-medium shrink-0">{ind.total}</span>
                            </button>
                        {/each}
                    {:else}
                        <div class="px-8 py-2 ml-2 border-l border-border-subtle">
                            <div class="w-3 h-3 border border-border border-t-text-muted rounded-full animate-spin"></div>
                        </div>
                    {/if}
                {/if}
            {/each}
          {/each}
        </div>
    {/if}
</div>

<style>
    .custom-scrollbar::-webkit-scrollbar { width: 6px; }
    .custom-scrollbar::-webkit-scrollbar-track { background: transparent; border-radius: 10px; }
    .custom-scrollbar::-webkit-scrollbar-thumb { background: var(--scrollbar-thumb); border-radius: 10px; min-height: 40px; }
    .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: var(--scrollbar-thumb-hover); }
</style>
