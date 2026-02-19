<!--
  LiveIndicators Component
  ========================
  Real-time prices via WebSocket with market hours awareness.
  
  The subtitle line ALWAYS renders at the same height (either real text
  or an invisible placeholder) so all three bottom panels align.
-->

<script>
    import { onMount, onDestroy } from 'svelte';
    import { getWebSocketUrl } from '$lib/config.js';
    import { marketIndex } from '$lib/stores.js';

    let { title = "INDICATORS", subtitle = "", symbols = [], dynamicByIndex = false } = $props();

    let quotes = $state({});
    let socket;
    let currentIndex = $derived($marketIndex);

    const MARKET_HOURS = {
        US:     { tz: 'America/New_York',  open: { h: 9, m: 30 }, close: { h: 16, m: 0 },  label: 'NYSE' },
        EU:     { tz: 'Europe/Amsterdam',  open: { h: 9, m: 0 },  close: { h: 17, m: 30 }, label: 'Euronext' },
        CRYPTO: { tz: 'UTC',              open: null, close: null, label: '24/7' },
        FOREX:  { tz: 'UTC',              open: null, close: null, label: 'FX' },
        COMMOD: { tz: 'America/New_York',  open: { h: 8, m: 20 }, close: { h: 13, m: 30 }, label: 'COMEX' },
    };

    const SYMBOL_MARKET_MAP = {
        'NVDA': 'US', 'AAPL': 'US', 'MSFT': 'US', 'AMZN': 'US',
        'ASML': 'EU', 'SAP': 'EU', 'LVMH': 'EU',
        'BINANCE:BTCUSDT': 'CRYPTO',
        'FXCM:EUR/USD': 'FOREX',
        'FXCM:XAU/USD': 'COMMOD',
    };

    const SYMBOL_SETS = {
        sp500:   { title: 'MARKET LEADERS', subtitle: 'S&P 500', symbols: ['NVDA', 'AAPL', 'MSFT'] },
        stoxx50: { title: 'MARKET LEADERS', subtitle: 'EURO STOXX 50', symbols: ['ASML', 'SAP', 'LVMH'] },
    };

    let resolvedSymbols = $derived(dynamicByIndex ? (SYMBOL_SETS[currentIndex]?.symbols || symbols) : symbols);
    let resolvedTitle = $derived(dynamicByIndex ? (SYMBOL_SETS[currentIndex]?.title || title) : title);
    let resolvedSubtitle = $derived(dynamicByIndex ? (SYMBOL_SETS[currentIndex]?.subtitle || '') : subtitle);

    // Does this panel have a real subtitle to display?
    let hasRealSubtitle = $derived(resolvedSubtitle && resolvedSubtitle.trim().length > 0);

    function clean(s) { return s && s.includes(':') ? s.split(':')[1] : s; }

    function isMarketOpen(symbol) {
        const marketKey = SYMBOL_MARKET_MAP[symbol] || SYMBOL_MARKET_MAP[clean(symbol)];
        const market = MARKET_HOURS[marketKey];
        if (!market || !market.open || !market.close) return true;
        const now = new Date();
        const tzNow = new Date(now.toLocaleString('en-US', { timeZone: market.tz }));
        const day = tzNow.getDay();
        const currentMins = tzNow.getHours() * 60 + tzNow.getMinutes();
        const openMins = market.open.h * 60 + market.open.m;
        const closeMins = market.close.h * 60 + market.close.m;
        return day >= 1 && day <= 5 && currentMins >= openMins && currentMins < closeMins;
    }

    function getStatus(symbol, data) {
        if (!data || data.price === 0) return { label: '', color: '', dot: 'opacity-0', pulse: false };
        if (isMarketOpen(symbol)) return { label: 'LIVE', color: 'text-green-500', dot: 'bg-green-500', pulse: true };
        return { label: 'CLOSED', color: 'text-red-500', dot: 'bg-red-500', pulse: false };
    }

    onMount(() => {
        const wsUrl = getWebSocketUrl();
        symbols.forEach(s => { quotes[s] = { price: 0, pct: 0, diff: 0, live: false }; });
        if (dynamicByIndex) {
            Object.values(SYMBOL_SETS).forEach(set => {
                set.symbols.forEach(s => { quotes[s] = { price: 0, pct: 0, diff: 0, live: false }; });
            });
        }

        socket = new WebSocket(wsUrl);
        socket.onmessage = (event) => {
            try {
                const update = JSON.parse(event.data);
                const allSymbols = dynamicByIndex
                    ? [...new Set(Object.values(SYMBOL_SETS).flatMap(s => s.symbols))]
                    : symbols;
                for (const s of allSymbols) {
                    if (s === update.symbol || clean(s) === update.symbol) {
                        quotes[s] = { price: update.price, pct: update.pct, diff: update.diff, live: update.live };
                        break;
                    }
                }
            } catch (e) { console.error("WS Error", e); }
        };
        socket.onerror = (e) => console.error("WS Connection Error:", e);
    });

    onDestroy(() => socket?.close());
</script>

<div class="flex flex-col h-full bg-white/5 rounded-3xl p-5 border border-white/5 shadow-2xl backdrop-blur-md overflow-hidden">

    <!-- Header: always renders both lines at consistent height -->
    <div class="flex flex-col items-start mb-4 border-b border-white/5 pb-3">
        <h3 class="text-[10px] font-black text-white/40 uppercase tracking-[0.3em]">{resolvedTitle}</h3>
        <!-- Always render the subtitle span at the same size. Invisible if no real subtitle. -->
        <span class="text-[11px] font-black uppercase tracking-wider mt-1
            {hasRealSubtitle ? 'text-bloom-accent' : 'text-transparent select-none'}"
        >{hasRealSubtitle ? resolvedSubtitle : 'PLACEHOLDER'}</span>
    </div>

    <div class="flex-1 grid grid-rows-3 gap-2">
        {#each resolvedSymbols as symbol}
            {@const data = quotes[symbol] || { price: 0, pct: 0, diff: 0 }}
            {@const status = getStatus(symbol, data)}
            {@const hasData = data.price > 0}

            <div class="flex items-center justify-between group py-1">
                <div class="flex flex-col">
                    <span class="text-sm font-black text-white uppercase tracking-normal">{clean(symbol)}</span>
                    <div class="flex items-center gap-1.5 mt-0.5">
                        <div class="w-1.5 h-1.5 rounded-full {status.dot} {status.pulse ? 'animate-pulse' : ''}"></div>
                        <span class="text-[9px] font-bold {status.color} uppercase tracking-tighter">{status.label}</span>
                    </div>
                </div>

                <div class="flex flex-col items-end">
                    <span class="text-base font-mono font-black text-white leading-none mb-1 tabular-nums">
                        {#if hasData}
                            ${data.price.toLocaleString(undefined, {
                                minimumFractionDigits: clean(symbol).includes('EUR/USD') ? 6 : 2,
                                maximumFractionDigits: clean(symbol).includes('EUR/USD') ? 6 : 2,
                            })}
                        {:else}
                            <span class="animate-pulse opacity-20">---</span>
                        {/if}
                    </span>

                    <div class="flex items-center gap-1.5 font-bold {data.pct >= 0 ? 'text-green-500' : 'text-red-500'}">
                        {#if hasData}
                            <span class="text-sm tabular-nums">
                                {data.pct >= 0 ? '+' : ''}{data.pct.toFixed(clean(symbol).includes('EUR/USD') ? 4 : 2)}%
                            </span>
                            <span class="text-[11px] opacity-70 tabular-nums font-black">
                                ({data.diff >= 0 ? '+' : ''}{data.diff.toFixed(clean(symbol).includes('EUR/USD') ? 6 : 2)})
                            </span>
                        {:else}
                            <span class="text-[10px] opacity-20">waiting...</span>
                        {/if}
                    </div>
                </div>
            </div>
        {/each}
    </div>
</div>