/**
 * =========================================================================
 *  Two-Tier Data Cache (SWR-style)
 * =========================================================================
 *  Tier 1 — In-memory LRU Map (instant, survives mode switches).
 *  Tier 2 — sessionStorage (slower, survives full page refresh).
 *
 *  When data is read, Tier 2 entries are promoted to Tier 1.
 *  The LRU cap (200 entries) prevents unbounded memory growth.
 * =========================================================================
 */

import { browser } from '$app/environment';

const _memCache = new Map();
const _MEM_CACHE_MAX = 200;

function _evictOldest() {
    if (_memCache.size <= _MEM_CACHE_MAX) return;
    const oldest = _memCache.keys().next().value;
    _memCache.delete(oldest);
}

export function getCached(key) {
    // Tier 1: in-memory (instant, LRU promotion)
    if (_memCache.has(key)) {
        const entry = _memCache.get(key);
        _memCache.delete(key);
        _memCache.set(key, entry);
        return entry;
    }
    // Tier 2: sessionStorage (survives refresh)
    if (browser) {
        try {
            const raw = sessionStorage.getItem('cache_' + key);
            if (raw) {
                const entry = JSON.parse(raw);
                _memCache.set(key, entry);
                _evictOldest();
                return entry;
            }
        } catch {}
    }
    return null;
}

export function setCached(key, data, ttlMs) {
    const entry = { data, ts: Date.now(), ttl: ttlMs };
    _memCache.delete(key);
    _memCache.set(key, entry);
    _evictOldest();
    if (browser) {
        try { sessionStorage.setItem('cache_' + key, JSON.stringify(entry)); } catch {}
    }
}

export function isCacheFresh(key) {
    const entry = getCached(key);
    if (!entry) return false;
    return (Date.now() - entry.ts) < entry.ttl;
}
