<script>
    import { browser } from '$app/environment';
    import { API_BASE_URL } from '$lib/config.js';
    import { overviewSelectedIndices } from '$lib/stores.js';

    let { currentPeriod = '1y', customRange = null } = $props();

    let stats = $state([]);
    let loading = $state(false);
    let cache = {};
    let now = $state(new Date());

    $effect(() => {
        if (!browser) return;
        const iv = setInterval(() => { now = new Date(); }, 30000);
        return () => clearInterval(iv);
    });

    const MARKET_HOURS = {
        'US':  { tz: 'America/New_York',  open: { h: 9, m: 30 }, close: { h: 16, m: 0 },  cet: '15:30 – 22:00' },
        'EU':  { tz: 'Europe/Berlin',     open: { h: 9, m: 0 },  close: { h: 17, m: 30 }, cet: '09:00 – 17:30' },
        'UK':  { tz: 'Europe/London',     open: { h: 8, m: 0 },  close: { h: 16, m: 30 }, cet: '09:00 – 17:30' },
        'JP':  { tz: 'Asia/Tokyo',        open: { h: 9, m: 0 },  close: { h: 15, m: 0 },  cet: '01:00 – 07:00' },
        'CN':  { tz: 'Asia/Shanghai',     open: { h: 9, m: 30 }, close: { h: 15, m: 0 },  cet: '02:30 – 08:00' },
        'IN':  { tz: 'Asia/Kolkata',      open: { h: 9, m: 15 }, close: { h: 15, m: 30 }, cet: '04:45 – 11:00' },
    };

    const INDEX_META = {
        '^GSPC':     { name: 'S&P 500',    flag: 'fi fi-us', color: '#e2e8f0', ccy: '$',  market: 'US' },
        '^STOXX50E': { name: 'STOXX 50',   flag: 'fi fi-eu', color: '#2563eb', ccy: '€',  market: 'EU' },
        '^FTSE':     { name: 'FTSE 100',   flag: 'fi fi-gb', color: '#ec4899', ccy: '£',  market: 'UK' },
        '^N225':     { name: 'Nikkei 225',  flag: 'fi fi-jp', color: '#f59e0b', ccy: '¥',  market: 'JP' },
        '000300.SS': { name: 'CSI 300',    flag: 'fi fi-cn', color: '#ef4444', ccy: '¥',  market: 'CN' },
        '^NSEI':     { name: 'Nifty 50',   flag: 'fi fi-in', color: '#22c55e', ccy: '₹',  market: 'IN' },
    };

    let selected = $derived(new Set($overviewSelectedIndices));

    function isMarketOpen(marketKey, time) {
        const market = MARKET_HOURS[marketKey];
        if (!market || !market.tz) return false;
        try {
            const tzNow = new Date(time.toLocaleString('en-US', { timeZone: market.tz }));
            const day = tzNow.getDay();
            if (day < 1 || day > 5) return false;
            const mins = tzNow.getHours() * 60 + tzNow.getMinutes();
            return mins >= (market.open.h * 60 + market.open.m) && mins < (market.close.h * 60 + market.close.m);
        } catch { return false; }
    }

    function fmtDate(d) {
        if (!d) return '';
        const dt = new Date(d + 'T00:00:00');
        return `${dt.getDate()} ${dt.toLocaleDateString('en-GB', { month: 'short' })} '${String(dt.getFullYear()).slice(2)}`;
    }

    function headerLabel(period, range) {
        if (range && range.start && range.end) return `${fmtDate(range.start)} → ${fmtDate(range.end)}`;
        if (!period) return '1Y';
        return period.toUpperCase();
    }
    let isCustom = $derived(!!(customRange && customRange.start));
    let periodTag = $derived(isCustom ? 'CUSTOM' : (currentPeriod || '1y').toUpperCase());

    async function load(period, range) {
        if (!browser) return;
        let url, cacheKey;
        if (range && range.start && range.end) {
            cacheKey = `stats_custom_${range.start}_${range.end}`;
            url = `${API_BASE_URL}/index-prices/stats?start=${range.start}&end=${range.end}&t=${Date.now()}`;
        } else {
            const p = period || '1y';
            cacheKey = `stats_${p}`;
            url = `${API_BASE_URL}/index-prices/stats?period=${p}&t=${Date.now()}`;
        }
        if (cache[cacheKey]) { stats = cache[cacheKey]; return; }
        loading = true;
        try {
            const controller = new AbortController();
            const timeout = setTimeout(() => controller.abort(), 10000);
            const res = await fetch(url, { signal: controller.signal });
            clearTimeout(timeout);
            if (res.ok) { const data = await res.json(); cache[cacheKey] = data; stats = data; }
        } catch (e) { /* silent */ }
        loading = false;
    }

    $effect(() => { load(currentPeriod, customRange); });

    let sortedStats = $derived(
        stats.filter(s => INDEX_META[s.symbol])
             .sort((a, b) => (b.period_return_pct || 0) - (a.period_return_pct || 0))
    );

    // Build a rank map: symbol -> rank index (0-5)
    let rankMap = $derived(
        Object.fromEntries(sortedStats.map((s, i) => [s.symbol, i]))
    );

    // All 6 symbols always rendered, positioned by rank
    let allStats = $derived(
        stats.filter(s => INDEX_META[s.symbol])
    );

    const COL = [13, 11, 10, 11, 8, 11, 9, 9, 18]; // % widths

    function fmt(val, decimals = 2) {
        if (val == null || isNaN(val)) return '—';
        return val.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    }
    function fmtPrice(val, ccy) {
        if (val == null) return '—';
        if (val >= 10000) return ccy + val.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
        return ccy + val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    function fmtCompact(val) {
        if (val == null) return '—';
        if (val >= 10000) return (val / 1000).toFixed(0) + 'k';
        return val.toLocaleString(undefined, { maximumFractionDigits: 0 });
    }
    function rangePosition(current, low, high) {
        if (high === low) return 50;
        return Math.min(100, Math.max(0, ((current - low) / (high - low)) * 100));
    }
</script>

<div class="perf-root h-full w-full flex flex-col bg-white/[0.03] rounded-2xl border border-white/5 overflow-hidden">
    <!-- Header -->
    <div class="flex items-center justify-between px-6 flex-shrink-0 header-row">
        <div class="flex items-center gap-3">
            <h3 class="hdr-title font-black text-white/30 uppercase">Index Performance</h3>
            <span class="hdr-period font-bold text-orange-400/80 uppercase tracking-wider">{headerLabel(currentPeriod, customRange)}</span>
        </div>
        {#if loading}
            <div class="w-3 h-3 border border-white/10 border-t-white/40 rounded-full animate-spin"></div>
        {/if}
    </div>

    <!-- Grid header -->
    <div class="grid-header flex items-center text-white/20 uppercase tracking-wider font-black px-2 flex-shrink-0">
        <div style="width:{COL[0]}%" class="pl-4 pr-1 text-left">Index</div>
        <div style="width:{COL[1]}%" class="px-1 text-left">Exchange</div>
        <div style="width:{COL[2]}%" class="px-1 text-right">Hours <span class="text-white/10 font-normal">(CET)</span></div>
        <div style="width:{COL[3]}%" class="px-1 text-right">PERIOD <span class="text-orange-400/90 period-tag">{periodTag}</span></div>
        <div style="width:{COL[4]}%" class="px-1 text-right">VOL <span class="text-orange-400/90 period-tag">{periodTag}</span></div>
        <div style="width:{COL[5]}%" class="px-1 text-right">Current Price</div>
        <div style="width:{COL[6]}%" class="px-1 text-right">Last Day</div>
        <div style="width:{COL[7]}%" class="px-1 text-right">YTD</div>
        <div style="width:{COL[8]}%" class="px-1 text-center">52W Range</div>
    </div>

    <!-- Rows container — relative, rows absolutely positioned via translateY -->
    <div class="flex-1 min-h-0 overflow-hidden px-2 relative rows-container">
        {#each allStats as row (row.symbol)}
            {@const meta = INDEX_META[row.symbol] || {}}
            {@const mkt = MARKET_HOURS[meta.market] || {}}
            {@const isSelected = selected.has(row.symbol)}
            {@const pos = rangePosition(row.current_price, row.low_52w, row.high_52w)}
            {@const open = isMarketOpen(meta.market, now)}
            {@const rank = rankMap[row.symbol] ?? 0}
            <div
                class="data-row absolute left-0 right-0 flex items-center border-t border-white/[0.04]"
                style="top: calc({rank} * (100% / 6)); height: calc(100% / 6); opacity: {isSelected ? 1 : 0.25}; transition: top 0.5s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.2s;"
            >
                <!-- Index -->
                <div style="width:{COL[0]}%" class="pl-4 pr-1">
                    <div class="flex items-center gap-2">
                        <div class="color-bar rounded-full flex-shrink-0" style="background: {meta.color}"></div>
                        <span class="{meta.flag || ''} fis rounded-sm flex-shrink-0 flag-icon"></span>
                        <span class="font-bold text-white/80 idx-name truncate">{meta.name || row.name}</span>
                    </div>
                </div>

                <!-- Exchange + status -->
                <div style="width:{COL[1]}%" class="px-1">
                    <div class="flex items-center gap-1.5">
                        <span class="font-bold text-white/40 exch-text">{row.exchange || '—'}</span>
                        <div class="flex items-center gap-1">
                            <div class="status-dot rounded-full flex-shrink-0 {open ? 'bg-green-500' : 'bg-red-500/60'}"
                                style="{open ? 'box-shadow: 0 0 5px rgba(34,197,94,0.5)' : ''}"></div>
                            <span class="status-text font-bold uppercase {open ? 'text-green-400/80' : 'text-red-400/40'}">{open ? 'LIVE' : 'CLOSED'}</span>
                        </div>
                    </div>
                </div>

                <!-- Hours CET -->
                <div style="width:{COL[2]}%" class="px-1 text-right text-white/25 font-mono tabular-nums font-medium hours-text">
                    {mkt.cet || '—'}
                </div>

                <!-- Period return -->
                <div style="width:{COL[3]}%" class="px-1 text-right font-mono tabular-nums font-black period-text"
                    style:color="{row.period_return_pct >= 0 ? 'rgba(34,197,94,1)' : 'rgba(239,68,68,0.95)'}">
                    {row.period_return_pct >= 0 ? '+' : ''}{fmt(row.period_return_pct)}%
                </div>

                <!-- Volatility -->
                <div style="width:{COL[4]}%" class="px-1 text-right font-mono tabular-nums text-white/40 font-bold val-text">
                    {fmt(row.volatility_pct, 1)}%
                </div>

                <!-- Current Price -->
                <div style="width:{COL[5]}%" class="px-1 text-right font-mono tabular-nums text-white/70 font-bold val-text">
                    {fmtPrice(row.current_price, meta.ccy || '')}
                </div>

                <!-- Last Day -->
                <div style="width:{COL[6]}%" class="px-1 text-right font-mono tabular-nums font-bold val-text"
                    style:color="{row.daily_change_pct >= 0 ? 'rgba(34,197,94,0.9)' : 'rgba(239,68,68,0.85)'}">
                    {row.daily_change_pct >= 0 ? '+' : ''}{fmt(row.daily_change_pct)}%
                </div>

                <!-- YTD -->
                <div style="width:{COL[7]}%" class="px-1 text-right font-mono tabular-nums font-bold val-text"
                    style:color="{row.ytd_return_pct >= 0 ? 'rgba(34,197,94,0.7)' : 'rgba(239,68,68,0.7)'}">
                    {row.ytd_return_pct >= 0 ? '+' : ''}{fmt(row.ytd_return_pct)}%
                </div>

                <!-- 52W Range -->
                <div style="width:{COL[8]}%" class="px-1">
                    <div class="flex items-center gap-1">
                        <span class="range-num text-white/20 font-mono tabular-nums text-right" style="min-width:32px">{fmtCompact(row.low_52w)}</span>
                        <div class="flex-1 range-track rounded-full relative overflow-hidden bg-white/[0.06]">
                            <div class="absolute inset-y-0 left-0 rounded-full" style="width:100%;background:linear-gradient(to right,rgba(239,68,68,0.2),rgba(34,197,94,0.2))"></div>
                            <div class="absolute top-1/2 range-dot rounded-full border-2 border-white/50"
                                style="left:{pos}%;transform:translate(-50%,-50%);background:{meta.color};box-shadow:0 0 4px {meta.color}44"></div>
                        </div>
                        <span class="range-num text-white/20 font-mono tabular-nums text-left" style="min-width:32px">{fmtCompact(row.high_52w)}</span>
                    </div>
                </div>
            </div>
        {/each}

        {#if allStats.length === 0 && !loading}
            <div class="absolute inset-0 flex items-center justify-center text-white/15 text-[11px] font-bold uppercase tracking-widest">No data available</div>
        {/if}
    </div>
</div>

<style>
    .perf-root { container-type: size; }
    .rows-container { overflow: hidden; }

    /* ---- Base / Small ---- */
    .header-row { padding-top: 6px; padding-bottom: 6px; border-bottom: 1px solid rgba(255,255,255,0.05); }
    .hdr-title { font-size: 11px; letter-spacing: 0.25em; }
    .hdr-period { font-size: 9px; }
    .grid-header { font-size: 9px; padding-top: 4px; padding-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.03); }
    .grid-header > div { white-space: nowrap; }
    .period-tag { font-size: 7px; letter-spacing: 0.05em; font-weight: 400; }
    .color-bar { width: 3px; height: 16px; }
    .flag-icon { font-size: 0.95rem !important; }
    .idx-name { font-size: 12px; }
    .exch-text { font-size: 11px; }
    .status-dot { width: 6px; height: 6px; }
    .status-text { font-size: 8px; letter-spacing: -0.02em; }
    .val-text { font-size: 12px; }
    .period-text { font-size: 13px; }
    .hours-text { font-size: 11px; }
    .range-num { font-size: 9px; }
    .range-track { height: 4px; }
    .range-dot { width: 7px; height: 7px; }

    /* ---- Medium (220–320px) ---- */
    @container (min-height: 220px) {
        .header-row { padding-top: 8px; padding-bottom: 8px; }
        .hdr-title { font-size: 12px; letter-spacing: 0.25em; }
        .hdr-period { font-size: 10px; }
        .grid-header { font-size: 10px; padding-top: 5px; padding-bottom: 5px; }
        .period-tag { font-size: 8px; }
        .color-bar { width: 3px; height: 20px; }
        .flag-icon { font-size: 1.1rem !important; }
        .idx-name { font-size: 13px; }
        .exch-text { font-size: 12px; }
        .status-dot { width: 7px; height: 7px; }
        .status-text { font-size: 9px; }
        .val-text { font-size: 13px; }
        .period-text { font-size: 14px; }
        .hours-text { font-size: 12px; }
        .range-num { font-size: 10px; }
        .range-track { height: 5px; }
        .range-dot { width: 8px; height: 8px; }
    }

    /* ---- Large (>320px) ---- */
    @container (min-height: 320px) {
        .header-row { padding-top: 12px; padding-bottom: 10px; }
        .hdr-title { font-size: 13px; letter-spacing: 0.3em; }
        .hdr-period { font-size: 11px; }
        .grid-header { font-size: 11px; padding-top: 6px; padding-bottom: 6px; }
        .period-tag { font-size: 9px; }
        .color-bar { width: 3px; height: 24px; }
        .flag-icon { font-size: 1.25rem !important; }
        .idx-name { font-size: 15px; }
        .exch-text { font-size: 14px; }
        .status-dot { width: 8px; height: 8px; }
        .status-text { font-size: 10px; }
        .val-text { font-size: 15px; }
        .period-text { font-size: 16px; }
        .hours-text { font-size: 14px; }
        .range-num { font-size: 11px; }
        .range-track { height: 7px; }
        .range-dot { width: 10px; height: 10px; }
    }
</style>