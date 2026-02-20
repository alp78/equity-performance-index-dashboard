<!--
  LiveIndicators — Real-time prices via WebSocket
-->

<script>
    import { onMount, onDestroy } from 'svelte';
    import { getWebSocketUrl, API_BASE_URL } from '$lib/config.js';
    import { marketIndex, currentCurrency, INDEX_CONFIG, selectedSymbol, summaryData, requestFocusSymbol } from '$lib/stores.js';

    let { title = "INDICATORS", subtitle = "", symbols = [], dynamicByIndex = false } = $props();

    let quotes = $state({});
    let lastUpdateTime = {};  // symbol → timestamp of last WS update
    let socket;
    let currentIndex = $derived($marketIndex);
    let indexCurrency = $derived($currentCurrency);

    const MARKET_HOURS = {
        US:     { tz: 'America/New_York',  open: { h: 9, m: 30 }, close: { h: 16, m: 0 },  weekdays: true },
        EU:     { tz: 'Europe/Amsterdam',  open: { h: 9, m: 0 },  close: { h: 17, m: 30 }, weekdays: true },
        UK:     { tz: 'Europe/London',     open: { h: 8, m: 0 },  close: { h: 16, m: 30 }, weekdays: true },
        JP:     { tz: 'Asia/Tokyo',        open: { h: 9, m: 0 },  close: { h: 15, m: 0 },  weekdays: true },
        CN:     { tz: 'Asia/Shanghai',     open: { h: 9, m: 30 }, close: { h: 15, m: 0 },  weekdays: true },
        IN:     { tz: 'Asia/Kolkata',      open: { h: 9, m: 15 }, close: { h: 15, m: 30 }, weekdays: true },
        CRYPTO: { tz: null },
        FOREX:  { tz: 'America/New_York',  open: { h: 17, m: 0 }, close: { h: 17, m: 0 }, weekdays: true, sundayOpen: true },
        COMMOD: { tz: 'America/New_York',  open: { h: 18, m: 0 }, close: { h: 17, m: 0 }, weekdays: true, sundayOpen: true },
    };

    const SYMBOL_MARKET_MAP = {
        'NVDA': 'US', 'AAPL': 'US', 'MSFT': 'US', 'AMZN': 'US',
        'ASML': 'EU', 'SAP': 'EU', 'LVMH': 'EU',
        'SHEL.L': 'UK', 'AZN.L': 'UK', 'HSBA.L': 'UK',
        '7203.T': 'JP', '6758.T': 'JP', '9984.T': 'JP',
        '600519.SS': 'CN', '000858.SZ': 'CN', '601318.SS': 'CN',
        'RELIANCE.NS': 'IN', 'TCS.NS': 'IN', 'INFY.NS': 'IN',
        'BINANCE:BTCUSDT': 'CRYPTO',
        'FXCM:EUR/USD': 'FOREX',
        'FXCM:XAU/USD': 'COMMOD',
    };

    // Per-symbol currency for non-dynamic panels
    const SYMBOL_CURRENCY = {
        'BINANCE:BTCUSDT': '$',
        'FXCM:XAU/USD': '$',
        'FXCM:EUR/USD': '',
    };

    const SYMBOL_SETS = {
        sp500:     { title: 'MARKET LEADERS', subtitle: 'S&P 500',       symbols: ['NVDA', 'AAPL', 'MSFT'] },
        stoxx50:   { title: 'MARKET LEADERS', subtitle: 'EURO STOXX 50', symbols: ['ASML', 'SAP', 'LVMH'] },
        ftse100:   { title: 'MARKET LEADERS', subtitle: 'FTSE 100',      symbols: ['SHEL.L', 'AZN.L', 'HSBA.L'] },
        nikkei225: { title: 'MARKET LEADERS', subtitle: 'Nikkei 225',    symbols: ['7203.T', '6758.T', '9984.T'] },
        csi300:    { title: 'MARKET LEADERS', subtitle: 'CSI 300',       symbols: ['600519.SS', '000858.SZ', '601318.SS'] },
        nifty50:   { title: 'MARKET LEADERS', subtitle: 'NIFTY 50',      symbols: ['RELIANCE.NS', 'TCS.NS', 'INFY.NS'] },
    };

    // Name lookup from sidebar data
    let nameMap = $derived(
        Object.fromEntries(($summaryData.assets || []).map(a => [a.symbol, a.name || '']))
    );

    // Resolve the actual ticker symbol in the sidebar for a leader symbol
    // e.g. 'ASML' display → 'ASML.AS' in sidebar
    const LEADER_TO_SIDEBAR = {
        'ASML': 'ASML.AS', 'SAP': 'SAP.DE', 'LVMH': 'MC.PA',
    };

    function selectLeaderSymbol(symbol) {
        if (!dynamicByIndex) return;
        const sidebarSymbol = LEADER_TO_SIDEBAR[symbol] || symbol;
        selectedSymbol.set(sidebarSymbol);
        requestFocusSymbol(sidebarSymbol);
    }

    function getLeaderName(symbol) {
        const sidebarSym = LEADER_TO_SIDEBAR[symbol] || symbol;
        return nameMap[sidebarSym] || nameMap[symbol] || '';
    }

    let resolvedSymbols = $derived(dynamicByIndex ? (SYMBOL_SETS[currentIndex]?.symbols || symbols) : symbols);
    let resolvedTitle = $derived(dynamicByIndex ? (SYMBOL_SETS[currentIndex]?.title || title) : title);
    let resolvedSubtitle = $derived(dynamicByIndex ? (SYMBOL_SETS[currentIndex]?.subtitle || '') : subtitle);
    let hasRealSubtitle = $derived(resolvedSubtitle && resolvedSubtitle.trim().length > 0);

    function clean(s) { return s && s.includes(':') ? s.split(':')[1] : s; }

    function getCurrency(symbol) {
        if (dynamicByIndex) return indexCurrency;
        return SYMBOL_CURRENCY[symbol] ?? '$';
    }

    function isMarketOpen(symbol) {
        const marketKey = SYMBOL_MARKET_MAP[symbol] || SYMBOL_MARKET_MAP[clean(symbol)];
        const market = MARKET_HOURS[marketKey];
        if (!market) return false;
        if (!market.tz) return true;

        const now = new Date();
        const tzNow = new Date(now.toLocaleString('en-US', { timeZone: market.tz }));
        const day = tzNow.getDay();
        const currentMins = tzNow.getHours() * 60 + tzNow.getMinutes();

        if (market.sundayOpen) {
            if (day === 6) return false;
            if (day === 0 && currentMins < market.open.h * 60 + market.open.m) return false;
            if (day === 5 && currentMins >= market.close.h * 60 + market.close.m) return false;
            return true;
        }

        if (day < 1 || day > 5) return false;
        const openMins = market.open.h * 60 + market.open.m;
        const closeMins = market.close.h * 60 + market.close.m;
        return currentMins >= openMins && currentMins < closeMins;
    }

    function getStatus(symbol, data) {
        if (!data || data.price === 0) return { label: '', color: '', dot: 'opacity-0', pulse: false };
        
        const marketKey = SYMBOL_MARKET_MAP[symbol] || SYMBOL_MARKET_MAP[clean(symbol)];
        
        // Crypto: always live if we're getting updates
        if (marketKey === 'CRYPTO') {
            const fresh = lastUpdateTime[symbol] && (Date.now() - lastUpdateTime[symbol] < 30000);
            if (fresh) return { label: 'LIVE', color: 'text-green-500', dot: 'bg-green-500', pulse: true };
            return { label: 'STALE', color: 'text-yellow-500', dot: 'bg-yellow-500', pulse: false };
        }
        
        // Stocks/Forex/Commodities: check market hours AND data freshness
        const marketOpen = isMarketOpen(symbol);
        const lastUpdate = lastUpdateTime[symbol];
        const fresh = lastUpdate && (Date.now() - lastUpdate < 120000); // 2 min
        
        if (marketOpen && fresh) return { label: 'LIVE', color: 'text-green-500', dot: 'bg-green-500', pulse: true };
        if (marketOpen) return { label: 'DELAYED', color: 'text-yellow-500', dot: 'bg-yellow-500', pulse: false };
        return { label: 'CLOSED', color: 'text-red-500', dot: 'bg-red-500', pulse: false };
    }

    onMount(() => {
        const wsUrl = getWebSocketUrl();
        // Init all symbols across all sets
        const allSyms = new Set([...symbols]);
        if (dynamicByIndex) {
            Object.values(SYMBOL_SETS).forEach(set => set.symbols.forEach(s => allSyms.add(s)));
        }
        allSyms.forEach(s => { quotes[s] = { price: 0, pct: 0, diff: 0, live: false }; });

        function connectWs() {
            socket = new WebSocket(wsUrl);
            socket.onopen = () => { console.log("WS connected"); };
            socket.onmessage = (event) => {
                try {
                    const update = JSON.parse(event.data);
                    const now = Date.now();
                    allSyms.forEach(s => {
                        if (s === update.symbol || clean(s) === update.symbol) {
                            quotes[s] = { price: update.price, pct: update.pct, diff: update.diff, live: update.live };
                            lastUpdateTime[s] = now;
                        }
                    });
                } catch (e) { console.error("WS Error", e); }
            };
            socket.onclose = () => {
                // Auto-reconnect after 3 seconds
                setTimeout(connectWs, 3000);
            };
            socket.onerror = (e) => {
                console.error("WS Error:", e);
                socket.close();
            };
        }

        connectWs();

        // Fetch market data immediately (in case WS takes a moment to connect)
        // Then poll every 60s as fallback for stale WS
        async function fetchMarketData() {
            try {
                const res = await fetch(`${API_BASE_URL}/market-data`);
                if (res.ok) {
                    const data = await res.json();
                    const now = Date.now();
                    if (data && typeof data === 'object') {
                        Object.values(data).forEach(update => {
                            allSyms.forEach(s => {
                                if (s === update.symbol || clean(s) === update.symbol) {
                                    quotes[s] = { price: update.price, pct: update.pct, diff: update.diff, live: update.live };
                                    lastUpdateTime[s] = now;
                                }
                            });
                        });
                    }
                }
            } catch (e) { /* silent fallback */ }
        }

        fetchMarketData(); // Immediate fetch
        const pollInterval = setInterval(fetchMarketData, 60000);

        return () => clearInterval(pollInterval);
    });

    onDestroy(() => socket?.close());
</script>

<div class="flex flex-col h-full bg-white/5 rounded-3xl p-5 border border-white/5 shadow-2xl backdrop-blur-md overflow-hidden">

    <div class="flex flex-col items-start mb-4 border-b border-white/5 pb-3">
        <h3 class="text-[10px] font-black text-white/40 uppercase tracking-[0.3em]">{resolvedTitle}</h3>
        <span class="text-[11px] font-black uppercase tracking-wider mt-1
            {hasRealSubtitle ? 'text-bloom-accent' : 'text-transparent select-none'}"
        >{hasRealSubtitle ? resolvedSubtitle : 'PLACEHOLDER'}</span>
    </div>

    <div class="flex-1 grid grid-rows-3 gap-2">
        {#each resolvedSymbols as symbol}
            {@const data = quotes[symbol] || { price: 0, pct: 0, diff: 0 }}
            {@const status = getStatus(symbol, data)}
            {@const hasData = data.price > 0}
            {@const ccy = getCurrency(symbol)}

            <div class="flex items-center justify-between group py-1">
                <div class="flex flex-col">
                    {#if dynamicByIndex}
                        <button
                            onclick={() => selectLeaderSymbol(symbol)}
                            title="{symbol}{getLeaderName(symbol) ? ' — ' + getLeaderName(symbol) : ''}"
                            class="text-sm font-black text-white/80 uppercase tracking-normal text-left hover:text-bloom-accent transition-colors cursor-pointer"
                        >{clean(symbol)}</button>
                    {:else}
                        <span class="text-sm font-black text-white/80 uppercase tracking-normal">{clean(symbol)}</span>
                    {/if}
                    <div class="flex items-center gap-1.5 mt-0.5">
                        <div class="w-1.5 h-1.5 rounded-full {status.dot} {status.pulse ? 'animate-pulse' : ''}"></div>
                        <span class="text-[9px] font-bold {status.color} uppercase tracking-tighter">{status.label}</span>
                    </div>
                </div>

                <div class="flex flex-col items-end">
                    <span class="text-base font-mono font-black text-white/85 leading-none mb-1 tabular-nums">
                        {#if hasData}
                            {ccy}{data.price.toLocaleString(undefined, {
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