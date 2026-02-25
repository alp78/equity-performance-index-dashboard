# Design Comparison — Global Exchange Monitor

Side-by-side analysis: current state vs best practice vs redesign plan for 9 categories.

---

## 1. Navigation

### Current State
In all page screenshots (StockMode.png, GlobalMacroMode.png, SectorRotationCrossIndex.png, SectorRotationSingleIndex.png), mode switching uses a **dropdown button** at the top of the sidebar. The button shows "GLOBAL MACRO", "SECTOR ROTATION", or an index name like "STOXX 50 EUR €" with a chevron. Clicking opens a floating panel listing all 8 options (2 special modes + 6 indices) in a flat list. There are no icons except inline SVGs for the globe and grid. The active mode is indicated by a faint left-border and a small dot — nearly invisible. The "GLOBAL EXCHANGE MONITOR" branding is tiny 13px letter-spaced muted text centered above the dropdown.

### Best Practice
**TradingView**: Left sidebar with icon-based tool rail (always visible), expandable panels for watchlist/alerts. Top toolbar for contextual controls (timeframe, indicators). Active tool has blue highlight.
**Koyfin**: Left sidebar with section links (Dashboard, Graph, Screener, etc.), each with an icon + label. Active section has blue accent background. Dashboard tabs at top for sub-views.
**Bloomberg Terminal**: Tabbed navigation with function keys. Modern redesign concepts (Dribbble/Behance) consistently show icon+text sidebar rails with clear grouping, accent-colored active states, and branded logo areas.
**Industry standard**: Sidebar 240-300px expanded / 48-64px collapsed. Nav items: icon (20-24px) + label, 40-48px height. Active: left accent border (3-4px) + highlighted background + accent icon color. Hover: 8-12% opacity white overlay.

### Redesign Plan
Replace the dropdown with a **persistent icon+text nav rail** inside the sidebar:
- **Branding area**: "GEM · Global Exchange Monitor" compact at top
- **3 mode buttons**: Globe (Global Macro), PieChart (Sector Rotation), TrendingUp (Stock Browsing) — using lucide-svelte icons
- **Active state**: 3px left accent bar (`--accent-primary`) + `--selected-overlay` background + accent-colored icon
- **Content panels**: Mode-specific content renders below the nav buttons in a scrollable area
- **Footer**: ThemeToggle pinned to bottom of sidebar
- Index selection (6 indices) moves inside the StockBrowser panel as a secondary dropdown — not mixed with mode switching

---

## 2. Index / Stat Cards

### Current State
In sidebar_globalMacro.png: index benchmarks displayed as a flat list with symbol, flag, price, and change %. No card boundaries, no sparklines, no visual separation between the 6 indices.
In indexPerformanceTable.png: data is in a table format — but no individual stat cards exist for key metrics.
In StockMode.png: the bottom panels (Most Active, Top Movers, etc.) each have hand-coded card wrappers (`bg-bg-card rounded-xl p-5 border border-border`) — functional but not using the design system's Card component.

### Best Practice
**TradingView**: Watchlist items have compact rows with ticker, price, change %, and optional mini-sparkline. Selected item highlighted with accent background.
**Koyfin**: Key metrics displayed in card rows with label (muted, uppercase, 11-12px) → large value (24-28px, monospace) → change indicator (colored, with arrow) → optional sparkline.
**Dell Design System / PatternFly**: Stat cards follow Label → Value → Change → Sparkline stack pattern. Cards have 1px subtle border, 8-12px radius, 16-24px padding. Hover: subtle brightening.

### Redesign Plan
- Adopt the existing `<Card>` component (currently unused) for all 12 panel components
- Card component enhanced with `fill` prop for grid-cell height filling
- Each card uses `<SectionHeader>` (also currently unused) for consistent title + subtitle
- Cards get consistent visual identity: `bg-surface-1`, `border-border-default`, `rounded-[--radius-lg]`, `p-[--space-xl]`
- No hand-coded card wrappers anywhere — single source of truth

---

## 3. Charts

### Current State
In chart_globalMacro.png: Multi-line comparison chart with reasonable styling. Grid lines present but slightly too visible — they compete with data lines at medium/low zoom. Axis labels are small and in muted text. The chart sits inside a card-like container with `bg-bg-card rounded-xl border border-border`.
In StockMode.png: OHLC candlestick chart with volume histogram. Tooltip uses custom-styled innerHTML with the design system's tooltip classes (tt-bg, tt-text, etc.) — good integration. Crosshair appears to use default solid line style.

### Best Practice
**TradingView**: Chart background matches the app background seamlessly. Grid lines are `rgba(255,255,255,0.04-0.06)` — barely visible. Axis labels in muted 11px. Crosshair uses dashed/dotted lines. Custom tooltips with dark surface backgrounds. Price scale labels highlighted with accent-colored backgrounds.
**Koyfin**: Similar approach — near-invisible grids, prominent data lines, monospace axis labels.
**AG Charts / Lightweight Charts dark themes**: Grid at 4% opacity white, axis text at 60% opacity, dotted crosshair.

### Redesign Plan
- Grid lines: `rgba(255,255,255,0.04)` for dark theme, `rgba(0,0,0,0.04)` for light
- Axis labels: 11-12px, 60% opacity, monospace font
- Crosshair: dotted style, 30% opacity
- Chart container: wrapped in `<Card padding={false}>` for consistent card boundaries
- Tooltip styling already uses design system classes — no changes needed

---

## 4. Tables / Rankings

### Current State
In indexPerformanceTable.png: Table with INDEX, PRICE, DAY, YTD, Δ HI, RETURN 5Y, VOL 5Y columns. Numbers appear right-aligned and monospace — good. But: no row hover effect, no alternating backgrounds, header row has no distinct styling (just muted text, no background), row heights are ~70px (wasteful), no horizontal separator lines.
In sectorHeatmap.png: Sector return grid with color-coded percentage cells. No alternating rows, no hover, no separators. Selected row indicator is a faint left border.

### Best Practice
**AG Grid**: Sticky headers with distinct background. Alternating row colors. Row hover highlight. Right-aligned numbers with tabular-nums. Sort indicators on headers. 44-48px row height.
**TradingView screener**: Thin horizontal lines (1px, low contrast), row hover with subtle background change, sticky first column, monospace numbers throughout.
**Data table best practices** (Pencil & Paper): Left-align text, RIGHT-ALIGN numbers. Header: muted, bold, uppercase, smaller font. Thin horizontal separators. 44-48px row height default.

### Redesign Plan
- Header row: `bg-surface-2` background, `font-bold uppercase text-[11px] tracking-widest`
- Row hover: `hover:bg-surface-2 transition-colors`
- Horizontal separators: `border-b border-border-subtle`
- Number alignment: right-aligned with `font-variant-numeric: tabular-nums`
- Row height: 44-48px (reduce from ~70px in current tables)
- Alternating row backgrounds in SectorHeatmap

---

## 5. Page Layout & Information Architecture

### Current State
In StockMode.png: Header (symbol + period controls) → Chart (flex-2) → Bottom panels (4 equal cols, flex-1). Chart takes ~65% of viewport. Bottom panels are cramped at ~25%.
In GlobalMacroMode.png: Header → 2-column grid: left (chart + table + correlation) / right (news + calendar). The layout is dense but functional. No metrics strip.
In SectorRotationCrossIndex.png: Header → Chart (flex-9) → Bottom (heatmap + stocks, flex-11). The bottom panels take MORE space than the chart — inverted hierarchy.

### Best Practice
**Bloomberg/Koyfin**: Header → Key Metrics Strip (scannable in 2 seconds) → Primary Content (largest element, ~60%+) → Secondary Content. F-pattern scanning — most important data top-left.
**Dashboard design principles**: 5-9 visualizations per view max. Inverted pyramid: summary → trend → detail. Consistent card-based panels with clear section headers.

### Redesign Plan
All modes follow: **Header → Metrics Strip → Primary Chart → Secondary Panels → Footer**
- Stock mode: Add `<StockMetrics>` component between header and chart (price, change, volume, range). Chart gets more space.
- Sector rotation: Fix flex ratios — chart flex-[3], bottom flex-[2] so chart dominates.
- Global macro: Wrap chart in `<Card>`. Add consistent spacing.
- Every section uses `<Card>` + `<SectionHeader>` for visual boundaries.

---

## 6. Filters & Controls

### Current State
In sidebar_sectorRotation_singleIndex.png and sidebar_sectorRotation_crossIndex.png: Checkboxes use **default browser styling** — the most visually inconsistent element in the entire app. The Cross-Index / Single-Index toggle uses custom pill-shaped tabs — one of the better elements.
Period selector buttons (1W, 1MO, etc.) are custom-styled pills with accent highlight for active state — functional and consistent.
The index selector dropdown is the same style as the mode dropdown — creating confusion about what's primary navigation vs. secondary filtering.

### Best Practice
**TradingView**: All controls are custom-styled to match the dark theme. Dropdowns use custom panels (not native selects). Checkboxes have custom styling (accent-colored checks on dark surface). Toggle switches are smooth with animation.
**Koyfin**: Consistent control styling — segmented buttons for view modes, custom dropdowns for filters, styled checkboxes throughout.

### Redesign Plan
- Checkboxes: Apply custom styling using CSS `appearance: none` + accent-colored check marks matching the design system
- The index dropdown stays in the StockBrowser/SectorPanel panels (secondary role), clearly distinct from the primary nav rail buttons
- Period selectors: already well-styled — no changes needed
- Cross-Index/Single-Index toggle: already well-styled — no changes needed

---

## 7. Typography & Number Display

### Current State
Fonts: Geist (UI) + Geist Mono (data), loaded via jsdelivr CDN, Inter as fallback. Fluid clamp() type scale with 8 size levels. Utility classes for display, heading, title, body, label, overline, mono, data.
Numbers appear to use monospace in most places. `font-variant-numeric: tabular-nums` is applied via `.text-mono`, `.text-data`, `.text-tabular` utility classes.
**Hierarchy issue**: In some places (sidebar stock list), all text is roughly the same size and weight — sector headers, industry headers, and stock rows lack sufficient differentiation.

### Best Practice
**Bloomberg**: Maximum 4 font sizes, 3 weights on any dashboard. Headlines 24-32px/600-700. Key numbers 20-28px/600. Body 14-16px/400. Labels 11-13px/400-500 uppercase with tracking.
**TradingView**: Monospace for all price/volume data. Bold weight draws attention to key numbers. Consistent sizing hierarchy.
**Industry standard**: `font-variant-numeric: tabular-nums lining-nums` everywhere numbers appear. 2 font families max.

### Redesign Plan
- Font pairing (Geist + Geist Mono) is **validated as excellent** — no change needed
- Ensure `tabular-nums` is applied to ALL number displays, not just those using `.text-data` classes
- Strengthen hierarchy in sidebar: sector headers larger/bolder than industry headers, which are larger/bolder than stock rows
- Type scale already exists and is well-designed — focus on consistent application rather than changes

---

## 8. Color & Visual Identity

### Current State
Dark theme (primary): Surface scale #0A0D12 → #12161F → #1A1F2B → #232938. Text hierarchy: primary E8ECF1, secondary 94A3B8, muted 5E6B80, disabled 3D4556. Positive: #10B981 (emerald), negative: #EF4444, warning: #F59E0B, accent: #3B82F6.
Light theme: Surface #F8FAFC → #FFFFFF → #F1F5F9 → #E2E8F0. Adjusted chart palette and text colors.
**Zero hardcoded hex colors in components** — all use CSS custom properties.

### Best Practice
**Bloomberg**: Blue (#0068FF) for positive, red (#FF433D) for negative — chosen for color-blind accessibility. Cyan for highlights. Black backgrounds.
**TradingView**: Near-black background (#131722), tonal elevation via surface lightness. Green/red for financial semantics. Blue accent.
**Industry consensus**: Avoid pure black (shadows invisible). Tonal elevation in dark mode (lighter = higher). Desaturated semantic colors (emerald not pure green).

### Redesign Plan
- The existing color system is **well-designed and research-validated** — no changes needed
- Emerald green (#10B981) for positive: validated across multiple sources
- Surface scale (#0A0D12 → #232938): matches TradingView/industry best practices
- Blue accent (#3B82F6): consistent with financial industry standards
- Focus: ensure every component actually uses the tokens (not hardcoded values)

---

## 9. Spacing & Visual Separation

### Current State
Page padding: `p-6` (24px). Card gaps: `gap-6` (24px) in stock mode, `gap-4` (16px) in sector/macro modes — inconsistent. Card internal padding: hand-coded `p-5` (20px) per component. Section spacing varies.
In StockMode.png: bottom panels feel cramped — flex-1 vs chart flex-2 gives them only ~33% of space.
In GlobalMacroMode.png: the 2-column grid is dense but readable.

### Best Practice
**TradingView/Koyfin**: Page padding 24-32px. Card gap 16-24px. Card padding 16-24px. Section spacing 32-48px between major groups.
**Dashboard design**: Content-out sizing — let content determine box size. 4px or 8px spacing grid. Consistent gaps across all views.

### Redesign Plan
- Standardize card gap to `gap-4` (16px) across all modes for consistency
- Card padding via `<Card>` component: `--space-xl` (20px) — consistent everywhere
- Section spacing between major groups: `gap-6` (24px)
- Stock mode: increase chart's flex ratio so bottom panels get adequate space
- Sector rotation: flip the current flex-9/flex-11 ratio to flex-3/flex-2 (chart dominant)
