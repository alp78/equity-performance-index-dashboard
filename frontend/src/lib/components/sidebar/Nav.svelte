<!--
  Nav — Sidebar navigation rail with mode buttons and theme toggle
  Replaces the old dropdown-based mode switcher with persistent icon+text buttons.
-->
<script>
    import { Globe, PieChart, TrendingUp } from 'lucide-svelte';
    import { isOverviewMode, isSectorMode } from '$lib/stores.js';
    import NavItem from '$lib/components/ui/NavItem.svelte';
    import ThemeToggle from '$lib/components/ui/ThemeToggle.svelte';

    let { onSwitchMode } = $props();

    let currentMode = $derived(
        $isOverviewMode ? 'macro' :
        $isSectorMode ? 'sectors' : 'stocks'
    );

    const modes = [
        { key: 'macro', label: 'Global Macro', icon: Globe },
        { key: 'stocks', label: 'Stock Browsing', icon: TrendingUp },
        { key: 'sectors', label: 'Sector Analysis', icon: PieChart },
    ];
</script>

<div class="sidebar-nav">
    <!-- branding -->
    <div class="sidebar-brand">
        <span class="brand-mark">GEM</span>
        <span class="brand-text">Global Exchange Monitor</span>
        <ThemeToggle />
    </div>

    <!-- mode buttons -->
    <div class="sidebar-modes">
        {#each modes as mode}
            <NavItem
                icon={mode.icon}
                label={mode.label}
                active={currentMode === mode.key}
                onclick={() => onSwitchMode(mode.key)}
            />
        {/each}
    </div>

    <!-- spacer -->
    <div class="sidebar-spacer"></div>
</div>

<style>
    .sidebar-nav {
        display: flex;
        flex-direction: column;
        padding-top: var(--space-lg);
        gap: var(--space-xs);
        flex-shrink: 0;
    }

    .sidebar-brand {
        display: flex;
        align-items: center;
        gap: var(--space-sm);
        padding: 0 var(--space-lg) var(--space-md);
    }

    .brand-mark {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 32px;
        height: 32px;
        border-radius: var(--radius-md);
        background: var(--accent-primary);
        color: white;
        font-size: 11px;
        font-weight: 800;
        letter-spacing: 0.05em;
        flex-shrink: 0;
    }

    .brand-text {
        font-size: 13px;
        font-weight: 600;
        color: var(--text-secondary);
        letter-spacing: 0.02em;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .sidebar-modes {
        display: flex;
        flex-direction: column;
        border-bottom: 1px solid var(--border-subtle);
        padding-bottom: var(--space-sm);
    }

    .sidebar-spacer {
        display: none;
    }

</style>
