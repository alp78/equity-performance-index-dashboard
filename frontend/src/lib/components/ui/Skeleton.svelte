<!--
  Skeleton — Loading placeholder with shimmer animation
  Variants: text, circle, rect, card
-->
<script>
    let {
        variant = 'text',
        width = '100%',
        height = null,
        lines = 1,
        class: className = '',
    } = $props();

    const defaultHeights = {
        text: '14px',
        circle: '40px',
        rect: '100px',
        card: '160px',
    };

    let h = $derived(height || defaultHeights[variant] || '14px');
</script>

{#if variant === 'text' && lines > 1}
    <div class="skeleton-group {className}" style="display:flex;flex-direction:column;gap:8px;width:{width};">
        {#each Array(lines) as _, i}
            <div
                class="skeleton skeleton-text"
                style="width:{i === lines - 1 ? '70%' : '100%'};height:{h};"
            ></div>
        {/each}
    </div>
{:else}
    <div
        class="skeleton skeleton-{variant} {className}"
        style="width:{variant === 'circle' ? h : width};height:{h};"
    ></div>
{/if}

<style>
    .skeleton {
        background: linear-gradient(
            90deg,
            var(--skeleton-base) 25%,
            var(--skeleton-shine) 50%,
            var(--skeleton-base) 75%
        );
        background-size: 200% 100%;
        animation: shimmer 1.5s linear infinite;
        border-radius: var(--radius-sm);
    }

    .skeleton-circle {
        border-radius: 50%;
    }

    .skeleton-card {
        border-radius: var(--radius-lg);
    }
</style>
