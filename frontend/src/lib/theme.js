/**
 * =========================================================================
 *  Design System — Color Tokens (Professional Terminal Theme)
 * =========================================================================
 *  Chart palettes, sector colors, and shade generators. Per-index brand
 *  colors and display names are now in config/indices.json and re-exported
 *  from index-registry.js.
 *
 *  Saturated, high-contrast palettes designed for dark backgrounds.
 *  Inspired by Bloomberg Terminal and TradingView Pro aesthetics.
 * =========================================================================
 */

// ── Sector Chart Palette ─────────────────────────────────────────────────
// 11 vivid, high-saturation hues for GICS sectors.  Every colour is
// punchy on dark backgrounds — no grays, no pastels.  Hues are spaced
// ~30° apart around the wheel for maximum perceptual distance.
//
// INDEX palette uses: Blue·Emerald·Red·Violet·Cyan·Amber  (6 hues)
// SECTOR palette uses the complementary gaps between them.
//
//  0 Pink      1 Yellow    2 Fuchsia   3 Orange    4 Green
//  5 Indigo    6 Sky       7 Lime      8 Teal      9 Rose    10 Violet

export const SECTOR_PALETTE = [
    '#EC4899',  // Pink        — Information Technology   (330°)
    '#EAB308',  // Yellow      — Financials              ( 50°)
    '#D946EF',  // Fuchsia     — Healthcare              (292°)
    '#F97316',  // Orange      — Industrials             ( 25°)
    '#22C55E',  // Green       — Consumer Discretionary  (142°)
    '#6366F1',  // Indigo      — Communication Services  (239°)
    '#0EA5E9',  // Sky         — Consumer Staples        (199°)
    '#84CC16',  // Lime        — Energy                  ( 84°)
    '#14B8A6',  // Teal        — Materials               (173°)
    '#F43F5E',  // Rose        — Utilities               (350°)
    '#A78BFA',  // Violet      — Real Estate             (258°)
];

// ── Sector Color Lookup ─────────────────────────────────────────────────
// Returns the palette color for a given sector name.

// Palette-order: indices match SECTOR_PALETTE positions
export const SECTOR_PALETTE_ORDER = [
    'Information Technology', 'Financials', 'Healthcare', 'Industrials',
    'Consumer Discretionary', 'Communication Services', 'Consumer Staples',
    'Energy', 'Materials', 'Utilities', 'Real Estate',
];

export function getSectorColor(sector) {
    const i = SECTOR_PALETTE_ORDER.indexOf(sector);
    return SECTOR_PALETTE[(i >= 0 ? i : SECTOR_PALETTE_ORDER.length) % SECTOR_PALETTE.length];
}

// ── Sector Name Abbreviations ─────────────────────────────────────────────
// Short labels for chart legends and compact UI where full GICS names are too long.

export const SECTOR_ABBREV = {
    'Information Technology': 'Info Tech',
    'Financials': 'Financials',
    'Healthcare': 'Health',
    'Industrials': 'Industls',
    'Consumer Discretionary': 'Cons Disc',
    'Communication Services': 'Comms',
    'Consumer Staples': 'Cons Stpl',
    'Materials': 'Materials',
    'Real Estate': 'Real Est',
    'Energy': 'Energy',
    'Utilities': 'Utilities',
};

// ── Industry Breakdown Palette ───────────────────────────────────────────
// Slightly muted variants for stacked industry-level charts so they don't
// overpower the sector-level colors above.

export const INDUSTRY_PALETTE = [
    '#5BA88A', '#C4955A', '#B86A7A', '#6A90A8', '#8AAC5A',
    '#B8965A', '#8A6AB0', '#A87A6A', '#7A9A78', '#5A8A9A',
    '#A8705A', '#6A7A9A', '#9A8A5A', '#7AACAC', '#9A6A8A',
];

// ── Sector Shade Generator ───────────────────────────────────────────────
// Generates N color shades from a sector color for donut chart slices.
// Varies lightness and saturation around the base hue so all slices
// belong to the same color family but are distinguishable.

export function getSectorShades(sectorColor, count) {
    // parse hex to RGB
    const hex = sectorColor.replace('#', '');
    const r = parseInt(hex.substring(0, 2), 16) / 255;
    const g = parseInt(hex.substring(2, 4), 16) / 255;
    const b = parseInt(hex.substring(4, 6), 16) / 255;

    // RGB to HSL
    const max = Math.max(r, g, b), min = Math.min(r, g, b);
    const l = (max + min) / 2;
    let h = 0, s = 0;
    if (max !== min) {
        const d = max - min;
        s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
        if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
        else if (max === g) h = ((b - r) / d + 2) / 6;
        else h = ((r - g) / d + 4) / 6;
    }

    // HSL to hex helper
    function hslToHex(h, s, l) {
        const hue2rgb = (p, q, t) => {
            if (t < 0) t += 1; if (t > 1) t -= 1;
            if (t < 1/6) return p + (q - p) * 6 * t;
            if (t < 1/2) return q;
            if (t < 2/3) return p + (q - p) * (2/3 - t) * 6;
            return p;
        };
        const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
        const p = 2 * l - q;
        const ri = Math.round(hue2rgb(p, q, h + 1/3) * 255);
        const gi = Math.round(hue2rgb(p, q, h) * 255);
        const bi = Math.round(hue2rgb(p, q, h - 1/3) * 255);
        return '#' + [ri, gi, bi].map(x => x.toString(16).padStart(2, '0')).join('');
    }

    const shades = [];
    // Adaptive lightness range — fewer slices = bigger spread
    const minL = 0.22;
    const maxL = count <= 3 ? 0.80 : count <= 6 ? 0.74 : count <= 10 ? 0.68 : 0.62;
    for (let i = 0; i < count; i++) {
        const t = count <= 1 ? 0.5 : i / (count - 1);
        const li = minL + t * (maxL - minL);
        // saturation: muted tones, slight taper for light shades
        const si = s * (0.45 - t * 0.12);
        // slight hue rotation for extra distinction
        const hi = (h + t * 0.08 - 0.04 + 1) % 1;
        shades.push(hslToHex(hi, si, li));
    }
    return shades;
}

