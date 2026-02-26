/**
 * =========================================================================
 *  Shared Formatting Utilities
 * =========================================================================
 *  Pure functions for consistent number/date formatting across components.
 * =========================================================================
 */

/** Short date: "26 Feb '26" */
export function fmtDate(d) {
    if (!d) return '';
    const dt = new Date(d + 'T00:00:00');
    return `${dt.getDate()} ${dt.toLocaleDateString('en-GB', { month: 'short' })} '${String(dt.getFullYear()).slice(2)}`;
}
