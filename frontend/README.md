# Frontend — SvelteKit Dashboard

Interactive financial dashboard for the Global Exchange Monitor, deployed on Firebase Hosting.

## Stack

- **SvelteKit 2** + **Svelte 5** (runes: `$state`, `$derived`, `$effect`, `$props`)
- **Tailwind CSS v4** with `@theme` directive
- **lightweight-charts** (TradingView) — candlestick/comparison charts
- **ECharts** — pie/donut charts (sector heatmap)
- **lucide-svelte** — icons
- **flag-icons** — country flags
- **Firebase Hosting** — static SPA deployment

## Local Development

```bash
# Install dependencies
npm install

# Start dev server (connects to backend on port 8000)
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview
```

## Environment Variables (optional)

Create `.env.local` for overrides:

```env
VITE_API_BASE_URL=https://your-custom-backend-url.run.app
```

## Dashboard Modes

1. **Index Benchmarks** — Global macro overview with index comparison, correlation heatmap, news feed
2. **Sector Analysis** — Cross-index or single-index sector rotation, industry breakdown
3. **Stock Browser** — Individual stock OHLCV chart, technical indicators, top movers, most active
4. **Macro Context** — Risk dashboard, BTC/Gold/EUR-USD tickers, economic calendar

## Project Structure

```
frontend/
├── src/
│   ├── app.html                    # HTML shell (fonts, GA, theme)
│   ├── routes/
│   │   ├── +page.svelte            # Main dashboard orchestrator
│   │   ├── +layout.svelte          # Layout (CSS imports)
│   │   └── layout.css              # Tailwind + design system entry
│   ├── lib/
│   │   ├── stores.js               # Svelte stores + data loaders
│   │   ├── config.js               # API URL resolution
│   │   ├── theme.js                # Color tokens + sector palette
│   │   ├── index-registry.js       # Index config from indices.json
│   │   ├── styles/
│   │   │   ├── tokens.css          # CSS custom properties
│   │   │   ├── global.css          # Resets, animations, scrollbar
│   │   │   ├── responsive.css      # 7-breakpoint responsive grid
│   │   │   ├── typography.css      # Fluid type scale
│   │   │   └── themes/
│   │   │       ├── dark.css        # Dark theme (primary)
│   │   │       └── light.css       # Light theme
│   │   ├── components/
│   │   │   ├── ui/                 # 13 primitive components
│   │   │   ├── sidebar/            # Sidebar decomposition
│   │   │   ├── PriceChart.svelte   # Candlestick/comparison chart
│   │   │   ├── TopMovers.svelte    # Top/bottom 3 movers
│   │   │   ├── MostActive.svelte   # Top 3 by volume
│   │   │   ├── TechnicalLevels.svelte
│   │   │   └── ...                 # 15+ panel components
│   │   └── ...
├── package.json
├── svelte.config.js                # adapter-static for Firebase
├── vite.config.js                  # Chunk splitting, $config alias
└── firebase.json                   # Hosting config (SPA rewrites)
```

## Deployment (Firebase)

```bash
npm run build
firebase deploy --only hosting
```

## Design System

See [DESIGN_SYSTEM.md](../DESIGN_SYSTEM.md) for the full specification including tokens, themes, typography, and responsive breakpoints.
