# Visual Audit — Global Exchange Monitor

Screenshot-by-screenshot critique of every `.png` in `frontend/src/lib/assets/`.

---

## Full-Page Screenshots

### StockMode.png
- **Navigation**: The mode selector is a dropdown button at the top of the sidebar reading "STOXX 50 EUR €" — indistinguishable from a form field. No icons, no visual weight. The "GLOBAL EXCHANGE MONITOR" branding above it is tiny 13px muted text with extreme letter-spacing — barely visible.
- **Sidebar stock list**: All sector headers (COMMUNICATION SERVICES, CONSUMER DISCRETIONARY, etc.) are the same visual weight — no color differentiation, no icons, no badges showing stock count. Expanded sectors show stocks with identical row styling — the currently selected stock has a faint background highlight that is almost imperceptible.
- **Chart area**: The OHLC chart dominates ~75% of the viewport. The chart itself has reasonable styling with the TradingView library defaults. The tooltip uses custom styled innerHTML (good). However, the chart has no card boundary — it blends into a rounded container with a very subtle border.
- **Bottom panels**: Four equal-width panels (Most Active, Top Movers, Technical Levels, Global Macro). Each has its own hand-coded card wrapper but they all look nearly identical in weight — no visual hierarchy distinguishes the more important panels from the less important ones. The panels are cramped vertically, taking only ~25% of the viewport height.
- **Header area**: The stock symbol "BAYN.DE" is in large uppercase text — good. Below it "BAYER AKTIENGESELLSCHAFT" and sector/industry labels in muted text — decent hierarchy. The period selector buttons (1W, 1MO, 3MO, etc.) are small pill-shaped buttons in the top-right — functional but visually disconnected from the chart.

### GlobalMacroMode.png
- **Navigation**: Same dropdown selector now reading "GLOBAL MACRO" with a globe SVG icon. The sidebar below shows a dense list of index benchmarks, bonds, commodities, rates, volatility, and FX rates — all in the same flat list with no visual separation between categories other than bold uppercase category headers.
- **Chart area**: Multi-line comparison chart with good color coding. Risk dashboard pills above the chart (VIX, EU VOL, MOVE, CURVE, CREDIT, USD) are well-designed with semantic coloring (green/amber/red). However, the pills feel disconnected from the chart — they float in the header area.
- **Right column**: News Feed and Economic Calendar stacked vertically. The news feed items have no visual separation — they blend into one continuous list. The economic calendar is clean but date group headers are weak.
- **Bottom-left**: Index Performance Table and Correlation Matrix side-by-side. The table has decent data but headers are not sticky and there's no row hover effect. The correlation matrix cells have subtle background coloring but the contrast range is too narrow — 0.15 and 0.77 look almost the same color.

### SectorRotationCrossIndex.png
- **Navigation**: Dropdown reads "SECTOR ROTATION". Below it, a Cross-Index / Single-Index toggle uses pill-shaped tabs — one of the better-styled elements. Index checkboxes below with country flags are functional.
- **Sidebar sector list**: Sectors listed with expand/collapse chevrons and industry sub-items with checkboxes. The selected sector "INDUSTRIALS" has a subtle blue left-border indicator — hard to spot.
- **Chart area**: Multi-line sector comparison chart with appropriate coloring. The legend is integrated into the chart area.
- **Bottom panels**: SectorHeatmap (8/12 cols) and TopStocks (4/12 cols). The heatmap is a table with colored percentage cells — functional but visually flat. All rows have the same height and no alternating backgrounds. The TopStocks panel shows horizontal bar charts — clean but basic.

### SectorRotationSingleIndex.png
- **Navigation**: Same dropdown with "SECTOR ROTATION" and Single-Index tab active. Below it, the index selector and sector tree with industry checkboxes.
- **Chart**: Single-index sector comparison with color-coded lines.
- **Bottom**: SectorRankings bar chart (left) and IndustryBreakdown pie chart (right). The pie chart uses ECharts with a donut layout — decent but the label placement is crowded for sectors with small percentages.

---

## Sidebar Screenshots

### Sidebar_stockMode.png / sidebar_stock.png
- The sidebar takes the full screen in these shots. "GLOBAL EXCHANGE MONITOR" in tiny centered muted text at top.
- Index selector dropdown: "STOXX 50 EUR €" with a chevron — plain button styling.
- Search bar: Standard input field with magnifying glass icon and up/down arrows — functional but unremarkable.
- Stock list: Expandable sectors → industries → stocks. Each stock row shows: symbol (bold), price (monospace), change %, high/low, volume. The data is well-organized but visually dense — all items at the same visual weight. Sector headers like "CONSUMER DISCRETIONARY" have a count badge on the right (12) which is helpful.
- Industry sub-headers (AUTO MANUFACTURERS, LUXURY GOODS, etc.) are in small muted caps — adequate but could be more visually distinct.

### sidebar_sectorRotation_singleIndex.png
- Shows the sector rotation sidebar in single-index mode. STOXX 50 selected with sector tree expanded. Sectors have checkboxes for toggling visibility. "CTRL+CLICK TO ISOLATE" hint text appears next to industry counts — useful but the text is very small and easy to miss.
- The checkbox styling uses default browser checkboxes — no custom styling. This is the most visually inconsistent element.

### sidebar_sectorRotation_crossIndex.png
- Cross-index mode showing index checkboxes (STOXX 50, S&P 500, FTSE 100, Nikkei 225, CSI 300, NIFTY 50) with country flags — visually clear. Selected indices have a highlighted background.
- Below: sector list with expand/collapse and industry checkboxes with counts. Same checkbox styling issue as single-index mode.

### sidebar_globalMacro.png
- Shows the full macro sidebar: INDEX BENCHMARKS (6 indices with prices and change %), MACRO CONTEXT section with BONDS, COMMODITIES, RATES, VOLATILITY, FX RATES subsections.
- Data is densely packed but well-organized. Positive changes in green, negative in red. Prices are right-aligned in monospace — good.
- Category headers (BONDS, COMMODITIES, etc.) are bold uppercase but have no background color or separator — they blend with the data rows below them.

---

## Component Screenshots

### indexPerformanceTable.png
- Clean table with INDEX, PRICE, DAY, YTD, Δ HI, RETURN 5Y, VOL 5Y columns. Index abbreviations (NIK, S&P, NFT, STX, FTSE, CSI) are color-coded — good.
- Numbers are in monospace font and appear right-aligned — correct. Positive returns in green, negative in red.
- **Issues**: No row hover effect. No alternating row backgrounds. The header row has no distinct background — it just uses muted text. Row heights are generous (~70px) which wastes vertical space. No horizontal separator lines between rows.

### correlationMatrix.png
- 6×6 matrix of Pearson daily return correlations. Index abbreviations colored along axes.
- Cell backgrounds use opacity-based coloring with a -1.0 to +1.0 legend at bottom.
- **Issues**: The color intensity range is too narrow — cells with 0.07 and 0.45 look almost the same dark shade. The diagonal cells show "—" which is correct. Cell borders are invisible — the grid relies entirely on spacing.

### newsFeed.png
- Vertical list of news items. Each has: country flag icon, headline text, index badge (S&P), source (Yahoo), and time (9h ago).
- "LIVE" indicator with green dot in top-right corner. Filter dropdowns for date and category.
- **Issues**: No visual separation between news items — they blend together. All items have the same visual weight. No hover state visible.

### economicCalendar.png
- Timeline grouped by date (TODAY, THU FEB 26, FRI FEB 27, MON MAR 2). Each event has: time, impact dot (orange/red), country flag, event name.
- **Issues**: Date group headers are just muted text with no background — easy to miss when scrolling. No impact legend is shown.

### chart_globalMacro.png
- Multi-line comparison chart showing NIKKEI 225 and NIFTY 50 normalized % change.
- Risk dashboard pills above: VIX, EU VOL, MOVE, CURVE, CREDIT, USD — each with semantic border coloring.
- **Issues**: Grid lines are slightly too visible — they compete with data lines. Y-axis labels are very small.

### sectorHeatmap.png
- Table showing sector returns across 4 indices (STX, S&P, NIK, NFT) with AVG column. Color-coded backgrounds for magnitude.
- **Issues**: No alternating row backgrounds. No row hover. No horizontal separators. Selected sector indicator (left border) is subtle.

### topStocks.png
- Horizontal bar chart: top 5 positive / bottom 5 negative stocks in sector. Green/red bars with return % labels.
- **Issues**: Hand-coded card wrapper. No hover effects on bars.

### mostActive.png
- Three stock entries with symbol, company name, price, change %, volume bar.
- **Issues**: Volume bars lack distinction — same gray as border. Card feels sparse.

### topMovers.png
- Top 3 gainers / bottom 3 losers horizontal bar chart with return % labels.
- **Issues**: Same as topStocks — hand-coded wrapper, no hover effects.

### technicalLevels.png
- Five indicators: RSI, MACD, BB %B, ATR, BETA with range gauges and status labels.
- Well-designed and compact. Color-coded status labels (green/amber/red).
- **Issues**: Range slider tracks are very small. MACD sig value in tiny text.

### globalMacroComponent.png
- Three live tickers: BTCUSDT, XAU/USD, EUR/USD with prices, changes, LIVE dots.
- **Issues**: Hand-coded card wrapper. No separators between items.
