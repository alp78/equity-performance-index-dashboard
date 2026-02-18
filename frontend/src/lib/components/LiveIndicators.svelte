<!--
  LiveIndicators Component
  ========================
  Displays real-time prices for a small set of key assets via WebSocket.

  Two modes:
    - Static:  always shows the same symbols (e.g. BTC, Gold, EUR/USD)
    - Dynamic: swaps symbols when the user switches market index
               (e.g. NVDA/AAPL/MSFT for S&P → ASML/SAP/LVMH for STOXX)

  The WebSocket connection is shared — one socket receives updates for
  all symbols, and this component filters for the ones it cares about.

  Props:
    title          — panel heading (e.g. "US EQUITIES", "GLOBAL MACRO")
    symbols        — default symbol list (used in static mode)
    dynamicByIndex — when true, symbols swap based on the active market index
-->

<script>
    import { onMount, onDestroy } from 'svelte';
    import { getWebSocketUrl } from '$lib/config.js';
    import { marketIndex } from '$lib/stores.js';

    // --- PROPS ---
    let { title = "INDICATORS", symbols = [], dynamicByIndex = false } = $props();

    // --- LOCAL STATE ---
    // Dictionary of symbol → { price, pct, diff, live }
    // Using a dict gives O(1) lookups when WebSocket messages arrive
    let quotes = $state({});
    let socket;
    let currentIndex = $derived($marketIndex);

    // --- DYNAMIC SYMBOL SETS ---
    // When dynamicByIndex is true, the displayed symbols change with the active index.
    // To add a new index, add an entry here — the component adapts automatically.
    const SYMBOL_SETS = {
        sp500:   { title: 'US EQUITIES', symbols: ['NVDA', 'AAPL', 'MSFT'] },
        stoxx50: { title: 'EU EQUITIES', symbols: ['ASML', 'SAP', 'LVMH'] },
    };

    // Resolve which symbols and title to actually display
    let resolvedSymbols = $derived(
        dynamicByIndex
            ? (SYMBOL_SETS[currentIndex]?.symbols || symbols)
            : symbols
    );

    let resolvedTitle = $derived(
        dynamicByIndex
            ? (SYMBOL_SETS[currentIndex]?.title || title)
            : title
    );

    // --- HELPERS ---

    // Strip exchange prefixes for display: "BINANCE:BTCUSDT" → "BTCUSDT"
    function clean(s) {
        return s && s.includes(':') ? s.split(':')[1] : s;
    }

    // Determine market status (LIVE / CLOSED) for the status dot indicator.
    // Priority: backend flag → crypto always-on → weekend check → assume live
    function getStatus(symbol, data) {
        // Still waiting for first data point — hide the dot entirely
        if (!data || data.price === 0) {
            return { label: '', color: '', dot: 'opacity-0', pulse: false };
        }

        const s = symbol.toUpperCase();

        // 1. Trust the backend's explicit live flag if present
        if (data.live !== undefined) {
            if (data.live === true) {
                return { label: 'LIVE', color: 'text-green-500', dot: 'bg-green-500', pulse: true };
            } else {
                return { label: 'CLOSED', color: 'text-red-500', dot: 'bg-red-500', pulse: false };
            }
        }

        // 2. Crypto markets are 24/7
        if (s.includes('BTC') || s.includes('ETH')) {
            return { label: 'LIVE', color: 'text-green-500', dot: 'bg-green-500', pulse: true };
        }

        // 3. Basic weekend check for traditional markets
        const day = new Date().getUTCDay();
        if (day === 0 || day === 6) {
            return { label: 'CLOSED', color: 'text-red-500', dot: 'bg-red-500', pulse: false };
        }

        // 4. Weekday — assume live
        return { label: 'LIVE', color: 'text-green-500', dot: 'bg-green-500', pulse: true };
    }

    // --- WEBSOCKET LIFECYCLE ---

    onMount(() => {
        const wsUrl = getWebSocketUrl();

        // Pre-fill placeholders so the UI renders "waiting..." instead of nothing
        symbols.forEach(s => { quotes[s] = { price: 0, pct: 0, diff: 0, live: false }; });

        // For dynamic mode, also initialize all possible index symbols
        if (dynamicByIndex) {
            Object.values(SYMBOL_SETS).forEach(set => {
                set.symbols.forEach(s => { quotes[s] = { price: 0, pct: 0, diff: 0, live: false }; });
            });
        }

        socket = new WebSocket(wsUrl);

        socket.onmessage = (event) => {
            try {
                const update = JSON.parse(event.data);

                // Build the full list of symbols this component listens for.
                // In dynamic mode, we listen for ALL indices' symbols so data is
                // already cached when the user switches index.
                const allSymbols = dynamicByIndex
                    ? [...new Set(Object.values(SYMBOL_SETS).flatMap(s => s.symbols))]
                    : symbols;

                // Match incoming update to our symbols (by full name or cleaned name)
                for (const s of allSymbols) {
                    if (s === update.symbol || clean(s) === update.symbol) {
                        quotes[s] = {
                            price: update.price,
                            pct: update.pct,
                            diff: update.diff,
                            live: update.live,
                        };
                        break;
                    }
                }
            } catch (e) {
                console.error("WS Error", e);
            }
        };

        socket.onerror = (e) => console.error("WS Connection Error:", e);
    });

    // Close the socket when navigating away to prevent memory leaks
    onDestroy(() => socket?.close());
</script>

<div class="flex flex-col h-full bg-white/5 rounded-3xl p-5 border border-white/5 shadow-2xl backdrop-blur-md overflow-hidden">

    <!-- Panel title -->
    <div class="flex justify-between items-center mb-4 border-b border-white/5 pb-3">
        <h3 class="text-[10px] font-black text-white/40 uppercase tracking-[0.3em]">{resolvedTitle}</h3>
    </div>

    <!-- One row per symbol: name + status dot | price + change -->
    <div class="flex-1 grid grid-rows-3 gap-2">
        {#each resolvedSymbols as symbol}
            {@const data = quotes[symbol] || { price: 0, pct: 0, diff: 0 }}
            {@const status = getStatus(symbol, data)}
            {@const hasData = data.price > 0}

            <div class="flex items-center justify-between group py-1">

                <!-- Left: symbol name + live/closed indicator -->
                <div class="flex flex-col">
                    <span class="text-sm font-black text-white uppercase tracking-normal">{clean(symbol)}</span>
                    <div class="flex items-center gap-1.5 mt-0.5">
                        <div class="w-1.5 h-1.5 rounded-full {status.dot} {status.pulse ? 'animate-pulse' : ''}"></div>
                        <span class="text-[9px] font-bold {status.color} uppercase tracking-tighter">{status.label}</span>
                    </div>
                </div>

                <!-- Right: price + percentage change + absolute change -->
                <div class="flex flex-col items-end">
                    <span class="text-base font-mono font-black text-white leading-none mb-1 tabular-nums">
                        {#if hasData}
                            <!-- EUR/USD needs 6 decimals; everything else uses 2 -->
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