<!--
  ═══════════════════════════════════════════════════════════════════════════
   EconomicCalendar — Upcoming High-Impact Releases
  ═══════════════════════════════════════════════════════════════════════════
   Shows next 4 weeks of key economic events (CPI, GDP, employment, etc.)
   grouped by date with sticky headers.  Impact level is colour-coded:
   red = high, amber = medium.  Country flags indicate the region.

   Data source : GET /macro/calendar  (FRED release dates, 15-min cache)
   Placement   : sidebar "Global Macro" mode, right column
  ═══════════════════════════════════════════════════════════════════════════
-->

<script>
    import { onMount, onDestroy } from 'svelte';
    import { API_BASE_URL } from '$lib/config.js';
    import { getCached, setCached, isCacheFresh } from '$lib/stores.js';
    import Card from '$lib/components/ui/Card.svelte';
    import SectionHeader from '$lib/components/ui/SectionHeader.svelte';

    let { currentPeriod = '1y', customRange = null } = $props();

    let events = $state([]);
    let loading = $state(true);
    let timer;
    let expandedIdx = $state(null);

    const COUNTRY_FLAGS = {
        'US': 'fi-us', 'EU': 'fi-eu', 'GB': 'fi-gb', 'JP': 'fi-jp',
        'CN': 'fi-cn', 'IN': 'fi-in', 'DE': 'fi-de', 'FR': 'fi-fr',
        'AU': 'fi-au', 'CA': 'fi-ca', 'CH': 'fi-ch', 'KR': 'fi-kr',
        'BR': 'fi-br', 'MX': 'fi-mx', 'NZ': 'fi-nz', 'SE': 'fi-se',
        'NO': 'fi-no', 'IT': 'fi-it', 'ES': 'fi-es',
    };

    function impactColor(impact) {
        if (impact === 'high') return 'bg-down';
        if (impact === 'medium') return 'bg-warn';
        return 'bg-border';
    }

    function dateLabel(dateStr) {
        if (!dateStr) return '';
        const d = new Date(dateStr);
        const now = new Date();
        const tomorrow = new Date(now);
        tomorrow.setDate(tomorrow.getDate() + 1);
        if (d.toDateString() === now.toDateString()) return 'Today';
        if (d.toDateString() === tomorrow.toDateString()) return 'Tomorrow';
        return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
    }

    function timeStr(dateStr) {
        if (!dateStr || dateStr.length < 16) return '';
        return dateStr.substring(11, 16);
    }

    function dateKey(dateStr) {
        if (!dateStr) return '';
        return dateStr.substring(0, 10);
    }

    let grouped = $derived.by(() => {
        const groups = [];
        let currentDate = null;
        for (const ev of events) {
            const dk = dateKey(ev.date);
            if (dk !== currentDate) {
                groups.push({ type: 'header', date: dk, label: dateLabel(ev.date) });
                currentDate = dk;
            }
            groups.push({ type: 'event', data: ev });
        }
        return groups;
    });

    const CALENDAR_TTL = 15 * 60 * 1000; // 15 min

    async function loadCalendar() {
        // Serve cached data instantly
        const cached = getCached('eco_calendar');
        if (cached) {
            events = cached.data;
            loading = false;
            if (isCacheFresh('eco_calendar')) return;
        }
        try {
            const res = await fetch(`${API_BASE_URL}/macro/calendar`);
            if (res.ok) {
                const data = await res.json();
                events = data;
                setCached('eco_calendar', data, CALENDAR_TTL);
            }
        } catch {}
        loading = false;
    }

    onMount(() => {
        loadCalendar();
        timer = setInterval(loadCalendar, 15 * 60 * 1000);
    });

    onDestroy(() => {
        if (timer) clearInterval(timer);
    });

    function toggleExpand(idx) {
        expandedIdx = expandedIdx === idx ? null : idx;
    }
</script>

<Card fill padding={false} class="cal-root min-h-0">
    <!-- header -->
    <div class="px-5 py-3 shrink-0">
        <SectionHeader title="Economic Calendar" subtitle="Next 4 weeks · Key releases" border>
            {#snippet action()}
                <svg class="w-4 h-4 text-text-faint" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/>
                </svg>
            {/snippet}
        </SectionHeader>
    </div>

    {#if loading}
        <div class="flex-1 flex items-center justify-center">
            <span class="text-[14px] text-text-faint animate-pulse">Loading calendar...</span>
        </div>
    {:else if events.length === 0}
        <div class="flex-1 flex items-center justify-center">
            <span class="text-[14px] text-text-faint">No upcoming events</span>
        </div>
    {:else}
        <div class="flex-1 overflow-y-auto overflow-x-hidden custom-scrollbar min-h-0">
            {#each grouped as item}
                {#if item.type === 'header'}
                    <div class="sticky top-0 z-10 px-4 py-2 bg-surface-2 border-b border-border-subtle">
                        <span class="date-hdr font-semibold uppercase text-text-muted">{item.label}</span>
                    </div>
                {:else}
                    {@const ev = item.data}
                    {@const idx = grouped.indexOf(item)}
                    {@const isExpanded = expandedIdx === idx}
                    <button class="cal-event-row w-full text-left border-b border-border-subtle hover:bg-bg-hover transition-colors cursor-pointer
                        {isExpanded ? 'bg-bg-hover' : ''}"
                        onclick={() => toggleExpand(idx)}
                    >
                        <div class="flex items-center gap-3 px-5 py-2.5">
                            <!-- impact dot -->
                            <div class="w-2.5 h-2.5 rounded-full shrink-0 {impactColor(ev.impact)}"></div>
                            <!-- time -->
                            <span class="time-text text-text-faint w-[42px] shrink-0 tabular-nums">{timeStr(ev.date)}</span>
                            <!-- country flag -->
                            {#if COUNTRY_FLAGS[ev.country]}
                                <span class="fi {COUNTRY_FLAGS[ev.country]} fis rounded-sm shrink-0 flag-icon"></span>
                            {:else}
                                <span class="w-5 h-3.5 shrink-0 bg-border-subtle rounded-sm text-[9px] text-text-faint flex items-center justify-center font-bold">{ev.country}</span>
                            {/if}
                            <!-- event name -->
                            <div class="flex-1 min-w-0">
                                <span class="event-text font-bold text-text-secondary truncate block">{ev.event}</span>
                            </div>
                            <!-- values: est / prev / actual -->
                            <div class="cal-values flex items-baseline gap-2 shrink-0">
                                {#if ev.actual != null}
                                    <span class="val-text font-bold tabular-nums text-text">{ev.actual}{ev.unit || ''}</span>
                                {:else if ev.estimate != null}
                                    <span class="val-text tabular-nums text-text-faint">est {ev.estimate}{ev.unit || ''}</span>
                                {/if}
                                {#if ev.prev != null}
                                    <span class="val-text tabular-nums text-text-faint">prev {ev.prev}{ev.unit || ''}</span>
                                {/if}
                            </div>
                            <!-- expand chevron -->
                            <svg class="w-3.5 h-3.5 text-text-faint shrink-0 transition-transform {isExpanded ? 'rotate-180' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
                            </svg>
                        </div>
                        <!-- expanded detail -->
                        {#if isExpanded && ev.description}
                            <div class="detail-panel px-5 pb-3 pt-0.5 ml-[58px]">
                                <div class="flex items-center gap-2 mb-1">
                                    <span class="text-[10px] font-bold uppercase tracking-wide {ev.impact === 'high' ? 'text-down' : 'text-warn'}">{ev.impact} impact</span>
                                </div>
                                <p class="text-[12px] text-text-muted leading-relaxed mb-2">{ev.description}</p>
                                {#if ev.link}
                                    <a href={ev.link} target="_blank" rel="noopener noreferrer"
                                       onclick={(e) => e.stopPropagation()}
                                       class="inline-flex items-center gap-1.5 text-[11px] font-semibold text-accent hover:text-accent/80 uppercase tracking-wider transition-colors">
                                        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/>
                                        </svg>
                                        View Source
                                    </a>
                                {/if}
                            </div>
                        {/if}
                    </button>
                {/if}
            {/each}
        </div>
    {/if}
</Card>

<style>
    :global(.cal-root)     { container-type: size; }

    .date-hdr     { font-size: clamp(10px, 1.4cqh, 12px); letter-spacing: 0.2em; }
    .time-text    { font-size: clamp(13px, 2cqh, 15px); }
    .flag-icon    { font-size: clamp(14px, 1.8cqh, 18px) !important; }
    .event-text   { font-size: clamp(12px, 1.8cqh, 15px); }
    .impact-text  { font-size: clamp(10px, 1.3cqh, 12px); }
    .val-text     { font-size: clamp(10px, 1.3cqh, 12px); }

    @container (max-width: 320px) {
        .cal-event-row { flex-wrap: wrap; }
        .cal-values { flex-wrap: wrap; gap: 4px; justify-content: flex-end; }
    }

    .detail-panel {
        animation: detail-in 0.15s ease-out;
    }

    @keyframes detail-in {
        from { opacity: 0; }
        to   { opacity: 1; }
    }

    .custom-scrollbar::-webkit-scrollbar { width: 4px; }
    .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
    .custom-scrollbar::-webkit-scrollbar-thumb { background: var(--scrollbar-thumb); border-radius: 10px; }
    .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: var(--scrollbar-thumb-hover); }
</style>
