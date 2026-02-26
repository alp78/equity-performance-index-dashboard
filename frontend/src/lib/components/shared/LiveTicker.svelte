<!--
  ═══════════════════════════════════════════════════════════════════════════
   LiveIndicators — Real-Time Price Ticker (WebSocket + HTTP Fallback)
  ═══════════════════════════════════════════════════════════════════════════
   Displays live instrument prices streamed via WebSocket (Binance BTC,
   yfinance macro symbols).  Falls back to HTTP polling (/market-data)
   every 60 s if WS fails.  Includes market open/closed/stale status
   badges with colour-coded pulsing indicators.

   When dynamicByIndex=true, the symbol set switches automatically to
   match the current market index (e.g. NVDA/AAPL/MSFT for S&P 500).

   Data source : WebSocket /ws  (primary, 10 s BTC / 30 s stocks)
                 GET /market-data  (HTTP fallback, 60 s poll)
   Placement   : bottom panel grid — "GLOBAL MACRO" instance
  ═══════════════════════════════════════════════════════════════════════════
-->

<script>
    import { onMount, onDestroy } from 'svelte';
    import Card from '$lib/components/ui/Card.svelte';
    import SectionHeader from '$lib/components/ui/SectionHeader.svelte';
    import { getWebSocketUrl, API_BASE_URL } from '$lib/config.js';
    import { marketIndex, currentCurrency, selectedSymbol, summaryData, requestFocusSymbol } from '$lib/stores.js';
    import { MARKET_HOURS, SYMBOL_MARKET_MAP, INDEX_EXCHANGE_INFO, INDEX_CONFIG } from '$lib/index-registry.js';

    let { title = "INDICATORS", subtitle = "", symbols = [], dynamicByIndex = false } = $props();

    let quotes = $state({});
    let lastUpdateTime = {};
    let socket;
    let destroyed = false;
    let currentIndex = $derived($marketIndex);
    let indexCurrency = $derived($currentCurrency);

    // Dynamic leaders fetched from /leaders API
    let dynamicLeaders = $state({});

    const SYMBOL_CURRENCY = {
        'BINANCE:BTCUSDT': '$',
        'FXCM:XAU/USD': '$',
        'FXCM:EUR/USD': '',
    };

    let nameMap = $derived(
        Object.fromEntries(($summaryData.assets || []).map(a => [a.symbol, a.name || '']))
    );

    function selectLeaderSymbol(symbol) {
        if (!dynamicByIndex) return;
        selectedSymbol.set(symbol);
        requestFocusSymbol(symbol);
    }

    function getLeaderName(symbol) {
        if (dynamicByIndex && dynamicLeaders[currentIndex]) {
            const leader = dynamicLeaders[currentIndex].find(l => l.symbol === symbol);
            if (leader?.name) return leader.name;
        }
        return nameMap[symbol] || '';
    }

    // --- RESOLVED PROPS ---

    let resolvedSymbols = $derived(
        dynamicByIndex
            ? (dynamicLeaders[currentIndex]?.map(l => l.symbol) || [])
            : symbols
    );
    let resolvedTitle = $derived(dynamicByIndex ? 'TOP ACTIVE' : title);
    let resolvedSubtitle = $derived(dynamicByIndex ? (INDEX_CONFIG[currentIndex]?.label || '') : subtitle);
    let hasRealSubtitle = $derived(resolvedSubtitle && resolvedSubtitle.trim().length > 0);

    // strip exchange prefix (e.g. "BINANCE:BTCUSDT" -> "BTCUSDT")
    function clean(s) { return s && s.includes(':') ? s.split(':')[1] : s; }

    function getCurrency(symbol) {
        if (dynamicByIndex) return indexCurrency;
        return SYMBOL_CURRENCY[symbol] ?? '$';
    }

    // --- MARKET STATUS ---

    function getMarketKey(symbol) {
        // Static map (macro instruments: BTC, XAU, EUR/USD)
        const staticKey = SYMBOL_MARKET_MAP[symbol] || SYMBOL_MARKET_MAP[clean(symbol)];
        if (staticKey) return staticKey;
        // Dynamic leaders: derive market code from index exchange info
        if (dynamicByIndex) return INDEX_EXCHANGE_INFO[currentIndex]?.marketCode || '';
        return '';
    }

    function isMarketOpen(symbol) {
        const marketKey = getMarketKey(symbol);
        const market = MARKET_HOURS[marketKey];
        if (!market) return false;
        if (!market.tz) return true;

        const now = new Date();
        const tzNow = new Date(now.toLocaleString('en-US', { timeZone: market.tz }));
        const day = tzNow.getDay();
        const currentMins = tzNow.getHours() * 60 + tzNow.getMinutes();

        // forex/commodities: open sunday evening through friday close
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

    // determine LIVE / DELAYED / CLOSED / STALE badge
    function getStatus(symbol, data) {
        if (!data || data.price === 0) return { label: '', color: '', dot: 'opacity-0', pulse: false };

        const marketKey = getMarketKey(symbol);

        if (marketKey === 'CRYPTO') {
            const fresh = lastUpdateTime[symbol] && (Date.now() - lastUpdateTime[symbol] < 30000);
            if (fresh) return { label: 'LIVE', color: 'text-up', dot: 'bg-up', pulse: true };
            return { label: 'STALE', color: 'text-warn', dot: 'bg-warn', pulse: false };
        }

        const marketOpen = isMarketOpen(symbol);
        const lastUpdate = lastUpdateTime[symbol];
        const fresh = lastUpdate && (Date.now() - lastUpdate < 120000);

        if (marketOpen && fresh) return { label: 'LIVE', color: 'text-up', dot: 'bg-up', pulse: true };
        if (marketOpen) return { label: 'DELAYED', color: 'text-warn', dot: 'bg-warn', pulse: false };
        return { label: 'CLOSED', color: 'text-down', dot: 'bg-down', pulse: false };
    }

    // --- WEBSOCKET + HTTP FALLBACK ---

    onMount(() => {
        const wsUrl = getWebSocketUrl();
        const allSyms = new Set([...symbols]);
        allSyms.forEach(s => { quotes[s] = { price: 0, pct: 0, diff: 0, live: false }; });

        // Fetch dynamic leaders (non-blocking — updates allSyms when ready)
        if (dynamicByIndex) {
            fetch(`${API_BASE_URL}/leaders`)
                .then(r => r.ok ? r.json() : {})
                .then(data => {
                    dynamicLeaders = data;
                    for (const leaders of Object.values(data)) {
                        for (const l of leaders) {
                            allSyms.add(l.symbol);
                            if (!quotes[l.symbol]) quotes[l.symbol] = { price: 0, pct: 0, diff: 0, live: false };
                        }
                    }
                })
                .catch(() => {});
        }

        let wsReconnectAttempts = 0;

        function connectWs() {
            if (destroyed) return;
            socket = new WebSocket(wsUrl);
            socket.onopen = () => { wsReconnectAttempts = 0; };
            socket.onmessage = (event) => {
                try {
                    const msg = JSON.parse(event.data);
                    if (msg.type === 'ping') return; // keepalive
                    const updates = Array.isArray(msg) ? msg : [msg];
                    const now = Date.now();
                    for (const update of updates) {
                        allSyms.forEach(s => {
                            if (s === update.symbol || clean(s) === update.symbol) {
                                quotes[s] = { price: update.price, pct: update.pct, diff: update.diff, live: update.live };
                                lastUpdateTime[s] = now;
                            }
                        });
                    }
                } catch (e) { console.error("WS Error", e); }
            };
            socket.onclose = () => {
                if (destroyed) return;
                const delay = Math.min(1000 * Math.pow(2, wsReconnectAttempts++), 30000);
                setTimeout(connectWs, delay);
            };
            socket.onerror = () => { socket.close(); };
        }

        connectWs();

        // immediate REST fetch + 60s poll as fallback for stale WS
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
            } catch (e) {}
        }

        fetchMarketData();
        const pollInterval = setInterval(fetchMarketData, 60000);

        return () => clearInterval(pollInterval);
    });

    onDestroy(() => {
        destroyed = true;
        socket?.close();
        lastUpdateTime = {};
    });
</script>

<Card fill class="live-ticker-root overflow-x-hidden">

    <!-- header -->
    <SectionHeader title={resolvedTitle} subtitle={hasRealSubtitle ? resolvedSubtitle : ''} border />


    <!-- symbol cards -->
    <div class="flex-1 flex flex-col overflow-y-auto overflow-x-hidden justify-around min-h-0">
        {#each resolvedSymbols as symbol}
            {@const data = quotes[symbol] || { price: 0, pct: 0, diff: 0 }}
            {@const status = getStatus(symbol, data)}
            {@const hasData = data.price > 0}
            {@const ccy = getCurrency(symbol)}

            <div class="ticker-row flex items-center justify-between group py-1 flex-shrink-0">
                <div class="flex flex-col">
                    {#if dynamicByIndex}
                        <button
                            onclick={() => selectLeaderSymbol(symbol)}
                            title="{symbol}{getLeaderName(symbol) ? ' — ' + getLeaderName(symbol) : ''}"
                            aria-label="Select {clean(symbol)}{getLeaderName(symbol) ? ' - ' + getLeaderName(symbol) : ''}"
                            class="text-[14px] font-semibold text-text uppercase tracking-tighter text-left hover:text-text transition-colors cursor-pointer"
                        >{clean(symbol)}</button>
                    {:else}
                        <span class="text-[14px] font-semibold text-text uppercase tracking-tighter">{clean(symbol)}</span>
                    {/if}
                    <div class="flex items-center gap-1.5 mt-0.5">
                        <div class="w-1.5 h-1.5 rounded-full {status.dot} {status.pulse ? 'animate-pulse' : ''}"></div>
                        <span class="text-[10px] font-bold {status.color} uppercase tracking-tighter">{status.label}</span>
                    </div>
                </div>

                <div class="ticker-price-col flex flex-col items-end">
                    <span class="text-[length:var(--text-num-lg)] font-bold text-text-secondary leading-none mb-1 tabular-nums">
                        {#if hasData}
                            <span class="text-text-muted">{ccy}</span>{data.price.toLocaleString(undefined, {
                                minimumFractionDigits: clean(symbol).includes('EUR/USD') ? 6 : 2,
                                maximumFractionDigits: clean(symbol).includes('EUR/USD') ? 6 : 2,
                            })}
                        {:else}
                            <span class="animate-pulse opacity-20">---</span>
                        {/if}
                    </span>

                    <div class="flex items-center gap-1.5 font-bold {(data.pct ?? 0) >= 0 ? 'text-up' : 'text-down'}">
                        {#if hasData}
                            <span class="text-[length:var(--text-num-sm)] tabular-nums">
                                {(data.pct ?? 0) >= 0 ? '+' : ''}{(data.pct ?? 0).toFixed(clean(symbol).includes('EUR/USD') ? 4 : 2)}%
                            </span>
                            <span class="text-[length:var(--text-num-xs)] opacity-70 tabular-nums font-bold">
                                ({(data.diff ?? 0) >= 0 ? '+' : ''}{(data.diff ?? 0).toFixed(clean(symbol).includes('EUR/USD') ? 6 : 2)})
                            </span>
                        {:else}
                            <span class="text-[11px] opacity-20">waiting...</span>
                        {/if}
                    </div>
                </div>
            </div>
        {/each}
    </div>
</Card>

<style>
    :global(.live-ticker-root) { container-type: inline-size; }

    @container (max-width: 280px) {
        .ticker-row { flex-direction: column; align-items: flex-start; gap: 4px; }
        .ticker-row .ticker-price-col { align-items: flex-start; }
    }
</style>