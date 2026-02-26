<!--
  ═══════════════════════════════════════════════════════════════════════════
   NewsFeed — Live Financial News with Date Picker & Index Filter
  ═══════════════════════════════════════════════════════════════════════════
   Auto-scrolling news ticker fed by Finnhub (company + general news).
   Features:
     • Date-picker calendar (Mon-start) with per-day article indicators
     • Index dropdown filter with per-index article counts
     • Incremental updates via /news/latest?since=... (60 s poll)
     • New-article flash animation (orange glow)
     • Auto-scroll pauses on hover, resumes after 3 s
     • Capped at 500 articles to prevent memory leaks

   Data source : GET /news  (full fetch, 5-min cache)
                 GET /news/latest?since=...  (incremental, 60 s poll)
   Placement   : sidebar "Global Macro" mode, right column
  ═══════════════════════════════════════════════════════════════════════════
-->

<script>
    import { onMount, onDestroy, tick } from 'svelte';
    import { API_BASE_URL } from '$lib/config.js';
    import { INDEX_CONFIG, INDEX_COLORS } from '$lib/index-registry.js';
    import { getCached, setCached, isCacheFresh } from '$lib/cache.js';
    import Card from '$lib/components/ui/Card.svelte';
    import SectionHeader from '$lib/components/ui/SectionHeader.svelte';

    // Hydrate from cache before first render — avoids "Loading news..." flash
    const _newsCache = getCached('news_feed');
    let articles = $state(_newsCache?.data || []);
    let loading = $state(!_newsCache);
    let connected = $state(!!_newsCache?.data?.length);
    let scrollContainer = $state(null);
    let autoScrolling = true;
    let animFrame;
    let scrollInterval;
    let pollTimer;
    let resumeTimer;

    // Track new articles for entrance animation (capped to prevent memory leak)
    let knownUrls = new Set(_newsCache?.data?.map(a => a.url) || []);
    const KNOWN_URLS_MAX = 5000;
    const ARTICLES_MAX = 500;
    let newArticleUrls = $state(new Set());
    let newClearTimer;

    // Filters
    let selectedDate = $state(null);   // null = all, or 'YYYY-MM-DD'
    let selectedIndex = $state(null);  // null = all
    let indexDropdownOpen = $state(false);
    let calendarOpen = $state(false);
    let calMonth = $state(new Date().getMonth());
    let calYear = $state(new Date().getFullYear());

    const INDEX_KEYS = Object.keys(INDEX_CONFIG);
    const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const DAY_HEADERS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];

    function toDateKey(unix) {
        const d = new Date(unix * 1000);
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }

    function timeAgo(unix) {
        const diff = Math.floor(Date.now() / 1000) - unix;
        if (diff < 60) return 'just now';
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
        return new Date(unix * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    }

    function dateLabel(unix) {
        const d = new Date(unix * 1000);
        const now = new Date();
        const yesterday = new Date(now);
        yesterday.setDate(yesterday.getDate() - 1);
        if (d.toDateString() === now.toDateString()) return 'Today';
        if (d.toDateString() === yesterday.toDateString()) return 'Yesterday';
        return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
    }

    // Set of date keys that have articles (respects index filter but not date filter)
    let articleDates = $derived.by(() => {
        const dates = new Set();
        const src = selectedIndex ? articles.filter(a => a.index === selectedIndex) : articles;
        for (const a of src) dates.add(toDateKey(a.datetime));
        return dates;
    });

    // Calendar grid (Monday-start)
    let calendarDays = $derived.by(() => {
        const first = new Date(calYear, calMonth, 1);
        let startDay = first.getDay() - 1; // Monday = 0
        if (startDay < 0) startDay = 6;    // Sunday = 6
        const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
        const cells = [];
        for (let i = 0; i < startDay; i++) cells.push(null);
        for (let d = 1; d <= daysInMonth; d++) {
            const key = `${calYear}-${String(calMonth + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
            cells.push({ day: d, key, has: articleDates.has(key) });
        }
        return cells;
    });

    // Has any articles in the currently viewed calendar month?
    let calMonthHasArticles = $derived.by(() => {
        for (const c of calendarDays) {
            if (c && c.has) return true;
        }
        return false;
    });

    // Earliest article month (to limit back-navigation)
    let earliestMonth = $derived.by(() => {
        if (articles.length === 0) return null;
        const last = articles[articles.length - 1];
        const d = new Date(last.datetime * 1000);
        return { y: d.getFullYear(), m: d.getMonth() };
    });

    let filteredArticles = $derived.by(() => {
        let result = articles;
        if (selectedDate) result = result.filter(a => toDateKey(a.datetime) === selectedDate);
        if (selectedIndex) result = result.filter(a => a.index === selectedIndex);
        return result;
    });

    // Per-index article counts (respects date filter but not index filter)
    let indexCounts = $derived.by(() => {
        const src = selectedDate ? articles.filter(a => toDateKey(a.datetime) === selectedDate) : articles;
        const counts = { _all: src.length };
        for (const key of INDEX_KEYS) counts[key] = 0;
        for (const a of src) { if (counts[a.index] !== undefined) counts[a.index]++; }
        return counts;
    });

    let groupedItems = $derived.by(() => {
        const items = [];
        let currentDate = null;
        for (const article of filteredArticles) {
            const label = dateLabel(article.datetime);
            if (label !== currentDate) {
                items.push({ type: 'separator', label });
                currentDate = label;
            }
            items.push({ type: 'article', data: article });
        }
        return items;
    });

    // Incremental rendering: start with a small batch, expand as user scrolls
    const RENDER_BATCH = 30;
    let renderLimit = $state(RENDER_BATCH);
    let sentinel = $state(null);
    let _observer = null;

    // Reset render limit when filters change
    $effect(() => {
        // track filter deps
        selectedDate; selectedIndex;
        renderLimit = RENDER_BATCH;
    });

    let visibleItems = $derived(groupedItems.slice(0, renderLimit));
    let hasMore = $derived(renderLimit < groupedItems.length);

    function loadMore() {
        if (renderLimit < groupedItems.length) {
            renderLimit = Math.min(renderLimit + RENDER_BATCH, groupedItems.length);
        }
    }

    $effect(() => {
        if (!sentinel) { _observer?.disconnect(); return; }
        _observer?.disconnect();
        _observer = new IntersectionObserver((entries) => {
            if (entries[0]?.isIntersecting) loadMore();
        }, { rootMargin: '200px' });
        _observer.observe(sentinel);
        return () => _observer?.disconnect();
    });

    // Pretty label for the date picker button
    let dateButtonLabel = $derived.by(() => {
        if (!selectedDate) return 'All dates';
        const d = new Date(selectedDate + 'T12:00:00');
        const now = new Date();
        const yesterday = new Date(now); yesterday.setDate(yesterday.getDate() - 1);
        if (d.toDateString() === now.toDateString()) return 'Today';
        if (d.toDateString() === yesterday.toDateString()) return 'Yesterday';
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    });

    function pickDate(key) {
        selectedDate = selectedDate === key ? null : key;
        calendarOpen = false;
        resetScroll();
    }
    function clearDate() {
        selectedDate = null;
        calendarOpen = false;
        resetScroll();
    }
    function prevMonth() {
        if (calMonth === 0) { calMonth = 11; calYear--; } else calMonth--;
    }
    function nextMonth() {
        const now = new Date();
        if (calYear === now.getFullYear() && calMonth >= now.getMonth()) return;
        if (calMonth === 11) { calMonth = 0; calYear++; } else calMonth++;
    }
    function pickIndex(key) {
        selectedIndex = key;
        indexDropdownOpen = false;
        resetScroll();
    }
    function resetScroll() {
        if (scrollContainer) scrollContainer.scrollTop = 0;
    }

    const TAG_COLORS = {
        'Earnings':     { text: 'text-[#22c55e]', bg: 'bg-[#22c55e]/10' },
        'Central Bank': { text: 'text-[#f59e0b]', bg: 'bg-[#f59e0b]/10' },
        'M&A':          { text: 'text-[#a855f7]', bg: 'bg-[#a855f7]/10' },
        'IPO':          { text: 'text-[#06b6d4]', bg: 'bg-[#06b6d4]/10' },
        'Macro':        { text: 'text-[#3b82f6]', bg: 'bg-[#3b82f6]/10' },
        'Commodities':  { text: 'text-[#eab308]', bg: 'bg-[#eab308]/10' },
        'Crypto':       { text: 'text-[#f97316]', bg: 'bg-[#f97316]/10' },
        'Tech':         { text: 'text-[#8b5cf6]', bg: 'bg-[#8b5cf6]/10' },
        'Regulation':   { text: 'text-[#ef4444]', bg: 'bg-[#ef4444]/10' },
        'Geopolitics':  { text: 'text-[#ec4899]', bg: 'bg-[#ec4899]/10' },
        'Markets':      { text: 'text-[#14b8a6]', bg: 'bg-[#14b8a6]/10' },
        'Energy':       { text: 'text-[#84cc16]', bg: 'bg-[#84cc16]/10' },
        'Healthcare':   { text: 'text-[#f43f5e]', bg: 'bg-[#f43f5e]/10' },
        'Finance':      { text: 'text-[#94a3b8]', bg: 'bg-[#94a3b8]/10' },
    };

    const NEWS_TTL = 5 * 60 * 1000; // 5 min
    let latestTimestamp = _newsCache?.data?.[0]?.datetime || 0;

    // Full load — used on mount; skips network if cache is fresh
    async function fetchNewsFull() {
        if (isCacheFresh('news_feed')) {
            // Cache was populated after component creation (e.g. by prefetchMacroWidgets)
            // — hydrate from it so we don't stay stuck on "Loading news..."
            const cached = getCached('news_feed');
            if (cached?.data?.length > 0 && articles.length === 0) {
                articles = cached.data;
                for (const a of articles) knownUrls.add(a.url);
                latestTimestamp = articles[0].datetime;
                connected = true;
            }
            loading = false;
            return;
        }
        try {
            const res = await fetch(`${API_BASE_URL}/news`);
            if (res.ok) {
                const data = await res.json();
                if (data.length > 0) {
                    for (const a of data) knownUrls.add(a.url);
                    articles = data;
                    latestTimestamp = data[0].datetime;
                    connected = true;
                    setCached('news_feed', data, NEWS_TTL);
                }
            }
        } catch {}
        loading = false;
    }

    // Diff poll — fetches only articles newer than latestTimestamp
    async function fetchNewsLatest() {
        if (!latestTimestamp) return;
        try {
            const res = await fetch(`${API_BASE_URL}/news/latest?since=${latestTimestamp}`);
            if (res.ok) {
                const data = await res.json();
                if (data.length > 0) {
                    const freshUrls = new Set();
                    const newArticles = [];
                    for (const a of data) {
                        if (!knownUrls.has(a.url)) {
                            knownUrls.add(a.url);
                            freshUrls.add(a.url);
                            newArticles.push(a);
                        }
                    }
                    if (newArticles.length > 0) {
                        // Merge new articles at the top, cap total
                        articles = [...newArticles, ...articles].slice(0, ARTICLES_MAX);
                        latestTimestamp = articles[0].datetime;
                        // Prune knownUrls if it grows too large
                        if (knownUrls.size > KNOWN_URLS_MAX) {
                            const keep = new Set(articles.map(a => a.url));
                            knownUrls = keep;
                        }

                        // Animate new articles
                        newArticleUrls = freshUrls;
                        pauseScroll();
                        if (scrollContainer) {
                            scrollContainer.scrollTo({ top: 0, behavior: 'smooth' });
                        }
                        if (newClearTimer) clearTimeout(newClearTimer);
                        newClearTimer = setTimeout(() => {
                            newArticleUrls = new Set();
                            autoScrolling = true;
                            startAutoScroll();
                        }, 4000);
                    }
                }
            }
        } catch {}
    }

    function startAutoScroll() {
        if (scrollInterval) clearInterval(scrollInterval);
        if (!scrollContainer) return;
        // skip if element is hidden (display:none → 0 dimensions)
        if (scrollContainer.clientHeight === 0) return;
        scrollInterval = setInterval(() => {
            if (!autoScrolling || !scrollContainer) return;
            if (scrollContainer.scrollHeight <= scrollContainer.clientHeight + 1) return;
            scrollContainer.scrollTop += 1;
            const nearBottom = scrollContainer.scrollTop >= scrollContainer.scrollHeight - scrollContainer.clientHeight - 200;
            if (nearBottom && renderLimit < groupedItems.length) {
                loadMore();
            }
            if (scrollContainer.scrollTop >= scrollContainer.scrollHeight - scrollContainer.clientHeight - 1) {
                scrollContainer.scrollTop = 0;
            }
        }, 50);
    }

    function pauseScroll() {
        autoScrolling = false;
        if (animFrame) cancelAnimationFrame(animFrame);
        if (scrollInterval) { clearInterval(scrollInterval); scrollInterval = null; }
        if (resumeTimer) clearTimeout(resumeTimer);
    }

    function resumeScroll() {
        if (resumeTimer) clearTimeout(resumeTimer);
        resumeTimer = setTimeout(() => {
            autoScrolling = true;
            startAutoScroll();
        }, 3000);
    }

    onMount(() => {
        fetchNewsFull().then(async () => { await tick(); startAutoScroll(); });
        pollTimer = setInterval(fetchNewsLatest, 60000);
    });

    onDestroy(() => {
        if (animFrame) cancelAnimationFrame(animFrame);
        if (scrollInterval) clearInterval(scrollInterval);
        if (pollTimer) clearInterval(pollTimer);
        if (resumeTimer) clearTimeout(resumeTimer);
        if (newClearTimer) clearTimeout(newClearTimer);
        _observer?.disconnect();
    });
</script>

<Card fill padding={false} class="news-feed-root">
    <!-- header -->
    <div class="px-4 py-2.5 shrink-0">
        <SectionHeader title="News Feed" border>
            {#snippet action()}
                {#if connected}
                    <div class="flex items-center gap-1.5">
                        <div class="w-1.5 h-1.5 rounded-full bg-up animate-pulse"></div>
                        <span class="text-[10px] font-bold text-text-faint uppercase">Live</span>
                    </div>
                {/if}
            {/snippet}
        </SectionHeader>
    </div>

    <!-- filters row -->
    <div class="flex items-center gap-2 px-4 pb-2 shrink-0">
        <!-- date picker -->
        <div class="relative flex-1 min-w-0">
            <button
                onclick={() => { calendarOpen = !calendarOpen; indexDropdownOpen = false; }}
                aria-label="Filter news by date"
                aria-expanded={calendarOpen}
                class="flex items-center gap-2 px-3 py-1.5 text-[12px] font-bold uppercase tracking-wider rounded-lg transition-colors w-full
                    {selectedDate ? 'bg-bg-active text-text-secondary' : 'bg-bg-hover text-text-faint hover:text-text-muted hover:bg-bg-active'}"
            >
                <svg aria-hidden="true" class="w-3.5 h-3.5 shrink-0 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
                <span class="truncate">{dateButtonLabel}</span>
                <svg aria-hidden="true" class="w-3 h-3 shrink-0 opacity-40 ml-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7"/></svg>
            </button>

            {#if calendarOpen}
                <!-- svelte-ignore a11y_no_static_element_interactions -->
                <div class="absolute left-0 top-full mt-1 z-30 bg-bg-card border border-border rounded-lg shadow-lg p-3 w-[240px]"
                     onmouseleave={() => { calendarOpen = false; }}>
                    <!-- month nav -->
                    <div class="flex items-center justify-between mb-2">
                        <button onclick={prevMonth} aria-label="Previous month"
                            class="w-5 h-5 flex items-center justify-center rounded hover:bg-bg-hover text-text-faint hover:text-text-secondary transition-colors
                                {earliestMonth && calYear === earliestMonth.y && calMonth <= earliestMonth.m ? 'opacity-20 pointer-events-none' : ''}"
                        >
                            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 19l-7-7 7-7"/></svg>
                        </button>
                        <span class="text-[11px] font-semibold text-text-muted uppercase tracking-wider">{MONTH_NAMES[calMonth]} {calYear}</span>
                        <button onclick={nextMonth} aria-label="Next month"
                            class="w-5 h-5 flex items-center justify-center rounded hover:bg-bg-hover text-text-faint hover:text-text-secondary transition-colors
                                {calYear === new Date().getFullYear() && calMonth >= new Date().getMonth() ? 'opacity-20 pointer-events-none' : ''}"
                        >
                            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7"/></svg>
                        </button>
                    </div>
                    <!-- day headers -->
                    <div class="grid grid-cols-7 gap-0.5 mb-1">
                        {#each DAY_HEADERS as dh}
                            <span class="text-[9px] font-bold text-text-faint text-center uppercase">{dh}</span>
                        {/each}
                    </div>
                    <!-- day cells -->
                    <div class="grid grid-cols-7 gap-0.5">
                        {#each calendarDays as cell}
                            {#if cell === null}
                                <div class="w-full aspect-square"></div>
                            {:else}
                                <button
                                    onclick={() => cell.has && pickDate(cell.key)}
                                    class="w-full aspect-square rounded text-[11px] font-bold flex items-center justify-center transition-colors relative
                                        {selectedDate === cell.key
                                            ? 'bg-text-faint text-text'
                                            : cell.has
                                                ? 'text-text hover:bg-bg-hover cursor-pointer'
                                                : 'text-text-faint cursor-default'}"
                                >
                                    {cell.day}
                                    {#if cell.has && selectedDate !== cell.key}
                                        <span class="absolute bottom-0.5 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full bg-text-faint/60"></span>
                                    {/if}
                                </button>
                            {/if}
                        {/each}
                    </div>
                    <!-- all dates -->
                    <button
                        onclick={clearDate}
                        class="w-full mt-2 px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-text-faint hover:text-text-muted bg-bg-hover hover:bg-bg-active rounded transition-colors
                            {selectedDate === null ? 'bg-bg-active text-text-secondary' : ''}"
                    >All dates</button>
                </div>
            {/if}
        </div>

        <!-- index dropdown -->
        <div class="relative shrink-0">
            <button
                onclick={() => { indexDropdownOpen = !indexDropdownOpen; calendarOpen = false; }}
                aria-label="Filter news by market index"
                aria-expanded={indexDropdownOpen}
                class="flex items-center gap-2 px-3 py-1.5 text-[12px] font-bold uppercase tracking-wider rounded-lg transition-colors
                    {selectedIndex ? 'bg-bg-active text-text-secondary' : 'bg-bg-hover text-text-faint hover:text-text-muted hover:bg-bg-active'}"
            >
                {#if selectedIndex}
                    <span aria-hidden="true" class="{INDEX_CONFIG[selectedIndex]?.flag || ''} fis rounded-sm" style="font-size: 0.8rem;"></span>
                    {INDEX_CONFIG[selectedIndex]?.abbr || selectedIndex} ({indexCounts[selectedIndex] || 0})
                {:else}
                    <svg aria-hidden="true" class="w-3.5 h-3.5 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z"/></svg>
                    All ({indexCounts._all || 0})
                {/if}
                <svg aria-hidden="true" class="w-3 h-3 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7"/></svg>
            </button>

            {#if indexDropdownOpen}
                <!-- svelte-ignore a11y_no_static_element_interactions -->
                <div
                    class="absolute right-0 top-full mt-1 z-30 bg-bg-card border border-border rounded-lg shadow-lg overflow-hidden min-w-[140px]"
                    onmouseleave={() => { indexDropdownOpen = false; }}
                >
                    <button
                        onclick={() => pickIndex(null)}
                        class="w-full flex items-center gap-2 px-3 py-1.5 text-[11px] font-bold text-text-faint hover:bg-bg-hover hover:text-text-secondary transition-colors
                            {selectedIndex === null ? 'bg-bg-active text-text-secondary' : ''}"
                    >All <span class="text-text-faint ml-auto">({indexCounts._all || 0})</span></button>
                    {#each INDEX_KEYS as key}
                        <button
                            onclick={() => pickIndex(key)}
                            class="w-full flex items-center gap-2 px-3 py-1.5 text-[11px] font-bold hover:bg-bg-hover transition-colors
                                {selectedIndex === key ? 'bg-bg-active' : ''}"
                            style="color: {INDEX_COLORS[key]}; opacity: {selectedIndex === key ? 1 : 0.7}"
                        >
                            <span aria-hidden="true" class="{INDEX_CONFIG[key]?.flag || ''} fis rounded-sm" style="font-size: 0.8rem;"></span>
                            {INDEX_CONFIG[key]?.abbr || key}
                            <span class="ml-auto text-text-faint">({indexCounts[key] || 0})</span>
                        </button>
                    {/each}
                </div>
            {/if}
        </div>
    </div>

    <!-- news list -->
    {#if loading}
        <div class="flex-1 flex items-center justify-center">
            <span class="text-[12px] text-text-faint animate-pulse">Loading news...</span>
        </div>
    {:else if filteredArticles.length === 0}
        <div class="flex-1 flex items-center justify-center">
            <span class="text-[12px] text-text-faint">No news for this filter</span>
        </div>
    {:else}
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
            bind:this={scrollContainer}
            onmouseenter={pauseScroll}
            onmouseleave={resumeScroll}
            class="flex-1 overflow-y-auto overflow-x-hidden custom-scrollbar min-h-0"
        >
            {#each visibleItems as item}
                {#if item.type === 'separator'}
                    <div class="sticky top-0 z-10 px-4 py-1.5 bg-bg-card/95 border-b border-border-subtle">
                        <span class="text-[10px] font-semibold uppercase tracking-[0.2em] text-text-muted">{item.label}</span>
                    </div>
                {:else}
                    <a
                        href={item.data.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        class="news-item flex items-start gap-3 px-4 py-3 border-b border-border-subtle hover:bg-bg-hover transition-all group
                            {newArticleUrls.has(item.data.url) ? 'news-flash' : ''}"
                    >
                        <div class="shrink-0 mt-0.5">
                            <span aria-hidden="true" class="{INDEX_CONFIG[item.data.index]?.flag || ''} fis rounded-sm" style="font-size: 1.1rem;"></span>
                        </div>
                        <div class="flex-1 min-w-0">
                            <p class="news-headline text-[13px] font-semibold text-text-secondary leading-snug line-clamp-2 group-hover:text-text transition-colors">
                                {item.data.headline}
                            </p>
                            <div class="news-meta flex items-center gap-2 mt-1.5 flex-wrap">
                                <span class="text-[10px] font-bold uppercase tracking-wider" style="color: {INDEX_COLORS[item.data.index] || '#f97316'}; opacity: 0.8">{INDEX_CONFIG[item.data.index]?.abbr || ''}</span>
                                <span class="text-[10px] text-text-faint">&middot;</span>
                                <span class="text-[10px] font-medium text-text-faint">{item.data.source}</span>
                                <span class="text-[10px] text-text-faint">&middot;</span>
                                <span class="text-[10px] font-medium text-text-faint">{timeAgo(item.data.datetime)}</span>
                                {#if item.data.tags?.length}
                                    {#each item.data.tags as tag}
                                        {@const tc = TAG_COLORS[tag] || { text: 'text-text-faint', bg: 'bg-bg-hover' }}
                                        <span class="news-tag px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider {tc.text} {tc.bg}">{tag}</span>
                                    {/each}
                                {/if}
                            </div>
                        </div>
                    </a>
                {/if}
            {/each}
            {#if hasMore}
                <div bind:this={sentinel} class="h-1 shrink-0"></div>
            {/if}
        </div>
    {/if}
</Card>

<style>
    :global(.news-feed-root) { container-type: inline-size; }

    .custom-scrollbar::-webkit-scrollbar { width: 4px; }
    .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
    .custom-scrollbar::-webkit-scrollbar-thumb { background: var(--scrollbar-thumb); border-radius: 10px; }
    .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: var(--scrollbar-thumb-hover); }

    @container (max-width: 280px) {
        .news-headline { font-size: 12px; -webkit-line-clamp: 2; line-clamp: 2; }
        .news-meta { font-size: 9px; }
        .news-item { padding-left: 12px; padding-right: 12px; padding-top: 8px; padding-bottom: 8px; gap: 8px; }
    }

    .line-clamp-2 {
        display: -webkit-box;
        -webkit-line-clamp: 2;
        line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
    }
    .news-flash {
        animation: newsSlideIn 0.5s ease-out, newsGlow 3s ease-in-out;
    }
    @keyframes newsSlideIn {
        from { opacity: 0; transform: translateY(-12px); }
        to   { opacity: 1; transform: translateY(0); }
    }
    @keyframes newsGlow {
        0%   { background: color-mix(in srgb, var(--accent-primary) 12%, transparent); }
        40%  { background: color-mix(in srgb, var(--accent-primary) 5%, transparent); }
        100% { background: transparent; }
    }
</style>
