<!--
  ThemeToggle — Dark/light mode switch
  Stores preference in localStorage as 'gem-theme'.
  Applied via data-theme attribute on <html>.
-->
<script>
    import { Sun, Moon } from 'lucide-svelte';
    import { browser } from '$app/environment';

    let theme = $state(browser ? (document.documentElement.getAttribute('data-theme') || 'dark') : 'dark');

    function toggle() {
        theme = theme === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', theme);
        document.documentElement.style.colorScheme = theme;
        localStorage.setItem('gem-theme', theme);
    }

    let isDark = $derived(theme === 'dark');
</script>

<button
    onclick={toggle}
    class="theme-toggle"
    aria-label="Toggle {isDark ? 'light' : 'dark'} theme"
    title="Switch to {isDark ? 'light' : 'dark'} mode"
>
    {#if isDark}
        <Sun size={16} strokeWidth={2} />
    {:else}
        <Moon size={16} strokeWidth={2} />
    {/if}
</button>

<style>
    .theme-toggle {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 32px;
        height: 32px;
        border-radius: var(--radius-md);
        border: 1px solid var(--border-subtle);
        background: var(--surface-2);
        color: var(--text-secondary);
        cursor: pointer;
        transition: all var(--transition-default);
    }
    .theme-toggle:hover {
        background: var(--surface-3);
        color: var(--text-primary);
        border-color: var(--border-default);
    }
    .theme-toggle:focus-visible {
        outline: 2px solid var(--accent-primary);
        outline-offset: 2px;
    }
</style>
