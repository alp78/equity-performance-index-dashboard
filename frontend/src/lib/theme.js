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

// Re-export from centralized registry (config/indices.json)
export { INDEX_COLORS, SECTOR_INDEX_NAMES } from '$lib/index-registry.js';

// ── Sector Chart Palette ─────────────────────────────────────────────────
// 11 maximally-distinct hues — one per colour family, ordered so the first
// 5-6 (most-commonly shown) are as far apart as possible.  Inspired by
// Tableau 10 / D3 Category10 principles adapted for dark backgrounds.
//
//  0 Blue      1 Orange    2 Pink      3 Violet    4 Teal
//  5 Yellow    6 Coral     7 Lime      8 Fuchsia   9 Slate    10 Cyan

export const SECTOR_PALETTE = [
    '#5B9CF6',  // Electric Blue    — Information Technology
    '#FB923C',  // Amber Orange     — Financials
    '#F472B6',  // Hot Pink         — Healthcare
    '#A78BFA',  // Soft Violet      — Industrials
    '#2DD4BF',  // Neon Teal        — Consumer Discretionary
    '#FACC15',  // Electric Yellow  — Communication Services
    '#FF7961',  // Coral Flame      — Consumer Staples
    '#A3E635',  // Neon Lime        — Energy
    '#E879F9',  // Electric Magenta — Materials
    '#94A3B8',  // Cool Slate       — Utilities
    '#22D3EE',  // Bright Cyan      — Real Estate
];

// ── Sector Color Lookup ─────────────────────────────────────────────────
// Returns the palette color for a given sector name.

const ALL_SECTORS = [
    'Information Technology', 'Financials', 'Healthcare', 'Industrials',
    'Consumer Discretionary', 'Communication Services', 'Consumer Staples',
    'Energy', 'Materials', 'Utilities', 'Real Estate',
];

export function getSectorColor(sector) {
    const i = ALL_SECTORS.indexOf(sector);
    return SECTOR_PALETTE[(i >= 0 ? i : ALL_SECTORS.length) % SECTOR_PALETTE.length];
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
    '#5B8DEF', '#D4915A', '#D46A6A', '#4AA88A', '#7ACC5A',
    '#C4A455', '#9B6AB0', '#CC7A85', '#A08060', '#8A9A78',
    '#4A90A8', '#B85050', '#7A5A9A', '#40A0A0', '#A86090',
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

