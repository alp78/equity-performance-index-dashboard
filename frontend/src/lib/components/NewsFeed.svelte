<script>
    import { onMount, onDestroy } from 'svelte';
    import { API_BASE_URL } from '$lib/config.js';

    let articles = $state([]);
    let loading = $state(true);
    let connected = $state(false);
    let scrollContainer = $state(null);
    let autoScrolling = true;
    let animFrame;
    let pollTimer;
    let resumeTimer;

    // Track new articles for entrance animation
    let knownUrls = new Set();
    let newArticleUrls = $state(new Set());
    let newClearTimer;

    // Filters
    let selectedDate = $state(null);   // null = all, or 'YYYY-MM-DD'
    let selectedIndex = $state(null);  // null = all
    let indexDropdownOpen = $state(false);
    let calendarOpen = $state(false);
    let calMonth = $state(new Date().getMonth());
    let calYear = $state(new Date().getFullYear());

    const INDEX_FLAG = {
        sp500: 'fi-us', stoxx50: 'fi-eu', ftse100: 'fi-gb',
        nikkei225: 'fi-jp', csi300: 'fi-cn', nifty50: 'fi-in',
    };
    const INDEX_LABEL = {
        sp500: 'S&P', stoxx50: 'STOXX', ftse100: 'FTSE',
        nikkei225: 'Nikkei', csi300: 'CSI', nifty50: 'NIFTY',
    };
    const INDEX_COLOR = {
        sp500: '#e2e8f0', stoxx50: '#2563eb', ftse100: '#ec4899',
        nikkei225: '#f59e0b', csi300: '#ef4444', nifty50: '#22c55e',
    };
    const INDEX_KEYS = ['sp500', 'stoxx50', 'ftse100', 'nikkei225', 'csi300', 'nifty50'];
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

    let latestTimestamp = 0; // tracks newest article timestamp for diff polling

    // Full load — used on mount only
    async function fetchNewsFull() {
        try {
            const res = await fetch(`${API_BASE_URL}/news`);
            if (res.ok) {
                const data = await res.json();
                if (data.length > 0) {
                    for (const a of data) knownUrls.add(a.url);
                    articles = data;
                    latestTimestamp = data[0].datetime;
                    connected = true;
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
                        // Merge new articles at the top
                        articles = [...newArticles, ...articles];
                        latestTimestamp = articles[0].datetime;

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
        if (!scrollContainer) return;
        const speed = 0.3;
        function step() {
            if (!autoScrolling || !scrollContainer) return;
            scrollContainer.scrollTop += speed;
            if (scrollContainer.scrollTop >= scrollContainer.scrollHeight - scrollContainer.clientHeight - 1) {
                scrollContainer.scrollTop = 0;
            }
            animFrame = requestAnimationFrame(step);
        }
        animFrame = requestAnimationFrame(step);
    }

    function pauseScroll() {
        autoScrolling = false;
        if (animFrame) cancelAnimationFrame(animFrame);
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
        fetchNewsFull().then(() => { startAutoScroll(); });
        pollTimer = setInterval(fetchNewsLatest, 60000);
    });

    onDestroy(() => {
        if (animFrame) cancelAnimationFrame(animFrame);
        if (pollTimer) clearInterval(pollTimer);
        if (resumeTimer) clearTimeout(resumeTimer);
        if (newClearTimer) clearTimeout(newClearTimer);
    });
</script>

<div class="flex flex-col h-full overflow-hidden">
    <!-- header -->
    <div class="flex items-center justify-between px-4 py-2.5 shrink-0">
        <h3 class="text-[10px] font-black text-white/40 uppercase tracking-[0.3em]">NEWS FEED</h3>
        {#if connected}
            <div class="flex items-center gap-1.5">
                <div class="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse"></div>
                <span class="text-[9px] font-bold text-white/30 uppercase">Live</span>
            </div>
        {/if}
    </div>

    <!-- filters row -->
    <div class="flex items-center gap-2 px-4 pb-2 shrink-0">
        <!-- date picker -->
        <div class="relative flex-1 min-w-0">
            <button
                onclick={() => { calendarOpen = !calendarOpen; indexDropdownOpen = false; }}
                class="flex items-center gap-2 px-3 py-1.5 text-[11px] font-bold uppercase tracking-wider rounded-lg transition-colors w-full
                    {selectedDate ? 'bg-white/15 text-white/60' : 'bg-white/5 text-white/30 hover:text-white/50 hover:bg-white/10'}"
            >
                <svg class="w-3.5 h-3.5 shrink-0 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
                <span class="truncate">{dateButtonLabel}</span>
                <svg class="w-3 h-3 shrink-0 opacity-40 ml-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7"/></svg>
            </button>

            {#if calendarOpen}
                <!-- svelte-ignore a11y_no_static_element_interactions -->
                <div class="absolute left-0 top-full mt-1 z-30 bg-[#1a1a22] border border-white/10 rounded-lg shadow-xl p-3 w-[240px]"
                     onmouseleave={() => { calendarOpen = false; }}>
                    <!-- month nav -->
                    <div class="flex items-center justify-between mb-2">
                        <button onclick={prevMonth} aria-label="Previous month"
                            class="w-5 h-5 flex items-center justify-center rounded hover:bg-white/10 text-white/40 hover:text-white/70 transition-colors
                                {earliestMonth && calYear === earliestMonth.y && calMonth <= earliestMonth.m ? 'opacity-20 pointer-events-none' : ''}"
                        >
                            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M15 19l-7-7 7-7"/></svg>
                        </button>
                        <span class="text-[10px] font-black text-white/50 uppercase tracking-wider">{MONTH_NAMES[calMonth]} {calYear}</span>
                        <button onclick={nextMonth} aria-label="Next month"
                            class="w-5 h-5 flex items-center justify-center rounded hover:bg-white/10 text-white/40 hover:text-white/70 transition-colors
                                {calYear === new Date().getFullYear() && calMonth >= new Date().getMonth() ? 'opacity-20 pointer-events-none' : ''}"
                        >
                            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 5l7 7-7 7"/></svg>
                        </button>
                    </div>
                    <!-- day headers -->
                    <div class="grid grid-cols-7 gap-0.5 mb-1">
                        {#each DAY_HEADERS as dh}
                            <span class="text-[8px] font-bold text-white/20 text-center uppercase">{dh}</span>
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
                                    class="w-full aspect-square rounded text-[10px] font-bold flex items-center justify-center transition-colors relative
                                        {selectedDate === cell.key
                                            ? 'bg-orange-500/80 text-white'
                                            : cell.has
                                                ? 'text-white/60 hover:bg-white/10 cursor-pointer'
                                                : 'text-white/10 cursor-default'}"
                                >
                                    {cell.day}
                                    {#if cell.has && selectedDate !== cell.key}
                                        <span class="absolute bottom-0.5 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full bg-orange-500/60"></span>
                                    {/if}
                                </button>
                            {/if}
                        {/each}
                    </div>
                    <!-- all dates -->
                    <button
                        onclick={clearDate}
                        class="w-full mt-2 px-2 py-1 text-[9px] font-bold uppercase tracking-wider text-white/30 hover:text-white/60 bg-white/5 hover:bg-white/10 rounded transition-colors
                            {selectedDate === null ? 'bg-white/15 text-white/50' : ''}"
                    >All dates</button>
                </div>
            {/if}
        </div>

        <!-- index dropdown -->
        <div class="relative shrink-0">
            <button
                onclick={() => { indexDropdownOpen = !indexDropdownOpen; calendarOpen = false; }}
                class="flex items-center gap-2 px-3 py-1.5 text-[11px] font-bold uppercase tracking-wider rounded-lg transition-colors
                    {selectedIndex ? 'bg-white/15 text-white/60' : 'bg-white/5 text-white/30 hover:text-white/50 hover:bg-white/10'}"
            >
                {#if selectedIndex}
                    <span class="fi {INDEX_FLAG[selectedIndex]} fis rounded-sm" style="font-size: 0.8rem;"></span>
                    {INDEX_LABEL[selectedIndex]} ({indexCounts[selectedIndex] || 0})
                {:else}
                    <svg class="w-3.5 h-3.5 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z"/></svg>
                    All ({indexCounts._all || 0})
                {/if}
                <svg class="w-3 h-3 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7"/></svg>
            </button>

            {#if indexDropdownOpen}
                <!-- svelte-ignore a11y_no_static_element_interactions -->
                <div
                    class="absolute right-0 top-full mt-1 z-30 bg-[#1a1a22] border border-white/10 rounded-lg shadow-xl overflow-hidden min-w-[140px]"
                    onmouseleave={() => { indexDropdownOpen = false; }}
                >
                    <button
                        onclick={() => pickIndex(null)}
                        class="w-full flex items-center gap-2 px-3 py-1.5 text-[10px] font-bold text-white/40 hover:bg-white/10 hover:text-white/70 transition-colors
                            {selectedIndex === null ? 'bg-white/10 text-white/60' : ''}"
                    >All <span class="text-white/25 ml-auto">({indexCounts._all || 0})</span></button>
                    {#each INDEX_KEYS as key}
                        <button
                            onclick={() => pickIndex(key)}
                            class="w-full flex items-center gap-2 px-3 py-1.5 text-[10px] font-bold hover:bg-white/10 transition-colors
                                {selectedIndex === key ? 'bg-white/10' : ''}"
                            style="color: {INDEX_COLOR[key]}; opacity: {selectedIndex === key ? 1 : 0.7}"
                        >
                            <span class="fi {INDEX_FLAG[key]} fis rounded-sm" style="font-size: 0.8rem;"></span>
                            {INDEX_LABEL[key]}
                            <span class="ml-auto" style="opacity: 0.5">({indexCounts[key] || 0})</span>
                        </button>
                    {/each}
                </div>
            {/if}
        </div>
    </div>

    <!-- news list -->
    {#if loading}
        <div class="flex-1 flex items-center justify-center">
            <span class="text-[11px] text-white/20 animate-pulse">Loading news...</span>
        </div>
    {:else if filteredArticles.length === 0}
        <div class="flex-1 flex items-center justify-center">
            <span class="text-[11px] text-white/20">No news for this filter</span>
        </div>
    {:else}
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <div
            bind:this={scrollContainer}
            onmouseenter={pauseScroll}
            onmouseleave={resumeScroll}
            class="flex-1 overflow-y-auto overflow-x-hidden custom-scrollbar min-h-0"
        >
            {#each groupedItems as item}
                {#if item.type === 'separator'}
                    <div class="sticky top-0 z-10 px-4 py-1.5 bg-bloom-card/95 backdrop-blur-sm border-b border-white/5">
                        <span class="text-[9px] font-black uppercase tracking-[0.2em] text-white/25">{item.label}</span>
                    </div>
                {:else}
                    <a
                        href={item.data.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        class="flex items-start gap-3 px-4 py-3 border-b border-white/[0.04] hover:bg-white/[0.04] transition-all group
                            {newArticleUrls.has(item.data.url) ? 'news-flash' : ''}"
                    >
                        <div class="shrink-0 mt-0.5">
                            <span class="fi {INDEX_FLAG[item.data.index] || ''} fis rounded-sm" style="font-size: 1.1rem;"></span>
                        </div>
                        <div class="flex-1 min-w-0">
                            <p class="text-[12px] font-semibold text-white/75 leading-snug line-clamp-2 group-hover:text-white/90 transition-colors">
                                {item.data.headline}
                            </p>
                            <div class="flex items-center gap-2 mt-1.5">
                                <span class="text-[9px] font-bold uppercase tracking-wider" style="color: {INDEX_COLOR[item.data.index] || '#f97316'}; opacity: 0.8">{INDEX_LABEL[item.data.index] || ''}</span>
                                <span class="text-[9px] text-white/25">&middot;</span>
                                <span class="text-[9px] font-medium text-white/30">{item.data.source}</span>
                                <span class="text-[9px] text-white/25">&middot;</span>
                                <span class="text-[9px] font-medium text-white/25">{timeAgo(item.data.datetime)}</span>
                            </div>
                        </div>
                    </a>
                {/if}
            {/each}
        </div>
    {/if}
</div>

<style>
    .custom-scrollbar::-webkit-scrollbar { width: 4px; }
    .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
    .custom-scrollbar::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 10px; }
    .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.2); }
    .line-clamp-2 {
        display: -webkit-box;
        -webkit-line-clamp: 2;
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
        0%   { background: rgba(249, 115, 22, 0.15); box-shadow: inset 0 0 20px rgba(249, 115, 22, 0.1); }
        40%  { background: rgba(249, 115, 22, 0.08); box-shadow: inset 0 0 10px rgba(249, 115, 22, 0.05); }
        100% { background: transparent; box-shadow: none; }
    }
</style>
