# Global Exchange Monitor — Design System

## Design Direction & Competitive Analysis

### Aesthetic Target
A **Bloomberg-meets-Koyfin** professional financial terminal: dark-first, data-dense but breathable, with clear information hierarchy.

### Competitive References

**TradingView** — Best in class for: chart interaction, dark theme implementation (19 color shades per hue), clean surface layering, tooltip design. We adopt: transparent chart backgrounds, crosshair-based tooltips, smooth theme toggle.

**Koyfin** — Best in class for: widget-based dashboards, clean data tables, professional typography, restrained color. We adopt: card-based layout with generous gaps, consistent section headers, clean table styling.

**Bloomberg Terminal (modernized)** — Best in class for: data density management, custom typeface for financial data (Matthew Carter's Bloomberg Font), tabbed panels, resizable windows. We adopt: strict type hierarchy, progressive disclosure via tabs/collapse, dense data only inside cards.

**Refinitiv Eikon** — Best in class for: two-tone themes ("charcoal"/"pearl"), Halo design system, consistent spacing. We adopt: dual theme with distinct elevation strategies (tonal for dark, shadow for light).

### Improvements Over Current Design (from Visual Audit)

| Problem | Reference Solution | Our Implementation |
|---------|-------------------|-------------------|
| No visual hierarchy | Bloomberg uses bold type scale with 4+ weight levels | Strict type scale with clamp() fluid sizing |
| Cramped bottom panels | Koyfin uses 16-24px gaps between widgets | `--space-xl` to `--space-2xl` card gaps |
| Generic Inter font | Bloomberg commissioned custom typeface | Geist + Geist Mono — purpose-built for data UIs |
| Everything visible at once | TradingView uses collapsible panels + tabs | Collapsible sections, tab groups within cards |
| No responsive design | Koyfin stacks widgets on mobile | Full breakpoint system 375px → 1920px+ |
| Hardcoded colors | Eikon uses Halo token system | Centralized CSS custom properties |
| Sidebar too dense | TradingView sidebar collapses on narrow | Hamburger overlay < 1024px, better section spacing |

---

## Core Principles
1. **Information hierarchy is king** — if everything is bold, nothing is bold
2. **Dark mode first** — financial professionals work in dark mode
3. **Color means something** — green/red for financial data only, never decorative
4. **Numbers must be scannable** — tabular-nums, monospace, proper alignment
5. **Restraint over decoration** — every element earns its place
6. **Theme system is source of truth** — all values from design tokens
7. **Responsive is not optional** — 375px to 1920px+, fluid between breakpoints
8. **Smooth is professional** — fluid scaling, no layout jumps

---

## Color System

### Dark Theme (Primary)

#### Surfaces (Tonal Elevation — lighter = higher)
| Token | Value | Usage |
|-------|-------|-------|
| `--surface-0` | `#0A0D12` | Page background, chart canvas |
| `--surface-1` | `#12161F` | Cards, panels, sidebar |
| `--surface-2` | `#1A1F2B` | Elevated cards, dropdowns, hover |
| `--surface-3` | `#232938` | Inputs, active states |
| `--surface-overlay` | `#1E2330` | Tooltips, popovers (+ backdrop-blur) |

#### Text Hierarchy
| Token | Value | Usage |
|-------|-------|-------|
| `--text-primary` | `#E8ECF1` | Prices, primary data, headings |
| `--text-secondary` | `#94A3B8` | Company names, descriptions |
| `--text-muted` | `#5E6B80` | Section headers, labels |
| `--text-disabled` | `#3D4556` | Timestamps, disabled |
| `--text-inverse` | `#0F172A` | On accent backgrounds |

#### Financial Semantics
| Token | Value | Usage |
|-------|-------|-------|
| `--color-positive` | `#10B981` | Emerald — gains, bullish (not neon) |
| `--color-positive-bg` | `rgba(16,185,129,0.10)` | Subtle positive tint |
| `--color-negative` | `#EF4444` | Losses, bearish |
| `--color-negative-bg` | `rgba(239,68,68,0.10)` | Subtle negative tint |
| `--color-warning` | `#F59E0B` | Amber — alerts, caution |
| `--color-warning-bg` | `rgba(245,158,11,0.10)` | Subtle warning tint |
| `--color-neutral` | `#64748B` | Unchanged, flat |
| `--color-info` | `#3B82F6` | Informational |
| `--color-info-bg` | `rgba(59,130,246,0.10)` | Subtle info tint |

#### Accent
| Token | Value | Usage |
|-------|-------|-------|
| `--accent-primary` | `#3B82F6` | Active states, links, focus rings |
| `--accent-secondary` | `#60A5FA` | Hover states |

#### Borders
| Token | Value | Usage |
|-------|-------|-------|
| `--border-subtle` | `#1A2030` | Barely visible separation |
| `--border-default` | `#243040` | Standard card borders |
| `--border-strong` | `#334155` | Emphasized, active |

### Light Theme (Secondary — shadow-based elevation)

#### Surfaces
| Token | Value |
|-------|-------|
| `--surface-0` | `#F8FAFC` |
| `--surface-1` | `#FFFFFF` |
| `--surface-2` | `#F1F5F9` |
| `--surface-3` | `#E2E8F0` |
| `--surface-overlay` | `#FFFFFF` |

#### Text
| Token | Value |
|-------|-------|
| `--text-primary` | `#0F172A` |
| `--text-secondary` | `#475569` |
| `--text-muted` | `#64748B` |
| `--text-disabled` | `#94A3B8` |
| `--text-inverse` | `#F8FAFC` |

#### Shadows (light theme only)
```css
--shadow-sm: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
--shadow-md: 0 4px 12px rgba(0,0,0,0.08);
--shadow-lg: 0 12px 32px rgba(0,0,0,0.12);
```

---

## Typography

### Font Pairing
- **UI/Headings**: **Geist** (by Vercel) — modern geometric sans-serif designed for digital/data interfaces
- **Numbers/Data**: **Geist Mono** — matched monospace for all numerical data

**Why Geist**: Purpose-built for developer/data tools. Distinctive but professional. Excellent small-size rendering. Matched mono variant ensures visual consistency. Not overused (unlike Inter/Roboto).

**Fallback**: `"Geist", "Inter", system-ui, -apple-system, sans-serif` and `"Geist Mono", "JetBrains Mono", ui-monospace, monospace`

### Type Scale (Fluid with clamp)
| Token | Size | Line-Height | Weight | Usage |
|-------|------|-------------|--------|-------|
| `--text-3xl` | clamp(1.75rem, 2vw+0.5rem, 2.25rem) | 1.2 | 700 | Hero metrics |
| `--text-2xl` | clamp(1.375rem, 1.5vw+0.5rem, 1.75rem) | 1.3 | 600 | Section titles |
| `--text-xl` | clamp(1.125rem, 1.2vw+0.4rem, 1.375rem) | 1.35 | 600 | Card titles |
| `--text-lg` | clamp(1rem, 1vw+0.35rem, 1.125rem) | 1.4 | 500 | Subsections |
| `--text-base` | clamp(0.8125rem, 0.85vw+0.25rem, 0.875rem) | 1.5 | 400 | Body text |
| `--text-sm` | clamp(0.75rem, 0.8vw+0.2rem, 0.8125rem) | 1.45 | 400 | Secondary |
| `--text-xs` | 0.75rem | 1.33 | 500 | Captions, labels |
| `--text-2xs` | 0.6875rem | 1.4 | 600 | Overlines (uppercase) |

### Rules
1. **Every number** → `font-family: var(--font-mono); font-variant-numeric: tabular-nums;`
2. **Prices, percentages, volumes** → monospace, always
3. **Headings** → `letter-spacing: -0.01em;` (slight tightening)
4. **Labels/overlines** → `text-transform: uppercase; letter-spacing: 0.05em;`

---

## Spacing Scale (4px grid)

| Token | Value | Usage |
|-------|-------|-------|
| `--space-2xs` | 2px | Micro spacing |
| `--space-xs` | 4px | Tight inline gaps |
| `--space-sm` | 8px | Within card sections |
| `--space-md` | 12px | Between related items |
| `--space-lg` | 16px | Card internal padding |
| `--space-xl` | 20px | Section padding, card gaps |
| `--space-2xl` | 24px | Major section gaps |
| `--space-3xl` | 32px | Between card groups |
| `--space-4xl` | 48px | Page-level sections |

---

## Border Radius Scale

| Token | Value | Usage |
|-------|-------|-------|
| `--radius-sm` | 6px | Badges, small elements |
| `--radius-md` | 8px | Buttons, inputs |
| `--radius-lg` | 12px | Cards, panels |
| `--radius-xl` | 16px | Large cards, modals |

---

## Chart Palette (8 colors, dark+light accessible)

| Token | Dark | Light | Name |
|-------|------|-------|------|
| `--chart-1` | `#3B82F6` | `#2563EB` | Blue |
| `--chart-2` | `#F97316` | `#EA580C` | Orange |
| `--chart-3` | `#10B981` | `#059669` | Emerald |
| `--chart-4` | `#8B5CF6` | `#7C3AED` | Violet |
| `--chart-5` | `#F59E0B` | `#D97706` | Amber |
| `--chart-6` | `#EC4899` | `#DB2777` | Pink |
| `--chart-7` | `#06B6D4` | `#0891B2` | Cyan |
| `--chart-8` | `#84CC16` | `#65A30D` | Lime |

---

## Index Brand Colors

| Index | Abbr | Color | Hex |
|-------|------|-------|-----|
| S&P 500 | S&P | Silver-White | `#E2E8F0` |
| EURO STOXX 50 | STX | Blue | `#3B82F6` |
| FTSE 100 | FTSE | Pink | `#EC4899` |
| Nikkei 225 | NIK | Amber | `#F59E0B` |
| CSI 300 | CSI | Red | `#EF4444` |
| NIFTY 50 | NFT | Green | `#22C55E` |

---

## Responsive Breakpoints

| Name | Range | Layout |
|------|-------|--------|
| Mobile | < 480px | Single column, bottom nav, compact cards |
| Mobile-lg | 480–767px | Single column, horizontal chart scroll |
| Tablet | 768–1023px | 2-column grid, hamburger nav |
| Laptop | 1024–1279px | 2-3 columns, sidebar overlay |
| Desktop | 1280–1439px | Full layout, sidebar visible |
| Wide | 1440–1919px | Full layout, generous spacing |
| Ultrawide | ≥ 1920px | Full 3-4 column grid |

---

## Animation & Transitions

| Token | Duration | Easing | Usage |
|-------|----------|--------|-------|
| `--transition-fast` | 100ms | ease-out | Hover states, button press |
| `--transition-default` | 200ms | ease-out | Tab switch, panel toggle |
| `--transition-slow` | 400ms | ease-out | Drawers, modals, page transitions |

### Animations
- **Skeleton shimmer**: 1.5s linear infinite background sweep
- **Price flash**: 600ms background-color fade (green/red → transparent)
- **Card entrance**: staggered 50ms delay, 300ms fadeInUp
- **Tab indicator**: 200ms width+position slide
- **`prefers-reduced-motion: reduce`**: all animations → 0.01ms

---

## Component Inventory

### UI Primitives (`/src/lib/components/ui/`)
| Component | Purpose |
|-----------|---------|
| Card | Surface container (default/elevated/outlined/glass) |
| Badge | Status indicators (positive/negative/neutral/warning/info) |
| Stat | Key metric: large value + label + change indicator |
| StatGroup | Responsive row/grid of Stats |
| Divider | Section separators (h/v) |
| Tabs | Animated tab switcher |
| Skeleton | Loading placeholder with shimmer |
| Tooltip | Design-system-styled tooltips |
| SectionHeader | Title + subtitle + action slot |
| ResponsiveGrid | auto-fit grid wrapper |
| EmptyState | Data unavailable placeholder |
| ThemeToggle | Dark/light mode switch |

### Feature Components (`/src/lib/components/`)
Chart, Sidebar, LiveIndicators, SectorHeatmap, SectorRankings, SectorTopStocks, RankingPanel, TechnicalLevels, CorrelationHeatmap, MacroWatchlist, RiskDashboard, EconomicCalendar, NewsFeed, IndustryBreakdown, IndexPerformanceTable, MarketLeaders

---

## Accessibility

- Green/red always paired with directional symbols (▲▼ or +/−)
- Minimum 4.5:1 contrast ratio for body text
- `prefers-reduced-motion: reduce` disables decorative animations
- Focus rings: 2px `--accent-primary` with 2px offset
- Touch targets: 44×44px minimum on mobile
- All interactive elements have `aria-label` attributes
