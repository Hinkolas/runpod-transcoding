<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { TooltipPlacement } from 'vidstack';

	let {
		placement = 'top',
		trigger,
		content
	}: { placement?: TooltipPlacement; trigger: Snippet; content: Snippet } = $props();
</script>

<media-tooltip>
	<media-tooltip-trigger>{@render trigger()}</media-tooltip-trigger>
	<media-tooltip-content
		{placement}
		class="z-50 rounded-md border border-zinc-700 bg-zinc-900/95 px-2 py-1 text-xs font-medium whitespace-nowrap text-zinc-100 opacity-0 shadow-lg transition-opacity duration-150 data-[visible]:opacity-100"
	>
		{@render content()}
	</media-tooltip-content>
</media-tooltip>
