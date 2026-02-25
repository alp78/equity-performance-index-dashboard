<!--
  Divider — Section separator (horizontal or vertical)
-->
<script>
    let {
        direction = 'horizontal',
        subtle = false,
        class: className = '',
    } = $props();

    let borderColor = $derived(subtle ? 'border-border-subtle' : 'border-border-default');
</script>

{#if direction === 'horizontal'}
    <div class="w-full border-t {borderColor} {className}" role="separator"></div>
{:else}
    <div class="h-full border-l {borderColor} {className}" role="separator"></div>
{/if}
