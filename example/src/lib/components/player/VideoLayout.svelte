<script lang="ts">
	import Play from '@lucide/svelte/icons/play';
	import Pause from '@lucide/svelte/icons/pause';
	import Volume2 from '@lucide/svelte/icons/volume-2';
	import Volume1 from '@lucide/svelte/icons/volume-1';
	import VolumeX from '@lucide/svelte/icons/volume-x';
	import Maximize from '@lucide/svelte/icons/maximize';
	import Minimize from '@lucide/svelte/icons/minimize';
	import PictureInPicture from '@lucide/svelte/icons/picture-in-picture';
	import PictureInPicture2 from '@lucide/svelte/icons/picture-in-picture-2';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Tooltip from './Tooltip.svelte';
	import SettingsMenu from './SettingsMenu.svelte';

	// Scrub-thumbnail WebVTT URL (worker's storyboard). null = no previews.
	let { thumbnails = null }: { thumbnails?: string | null } = $props();

	const iconButton =
		'group inline-flex size-10 shrink-0 cursor-pointer items-center justify-center rounded-md text-white outline-none ring-indigo-400 transition hover:bg-white/15 data-[focus]:ring-2 aria-hidden:hidden';
</script>

<!-- Gestures: click toggles play, double-click toggles fullscreen, double-tap edges seek ±10s -->
<media-gesture class="absolute inset-0 z-0 block" event="pointerup" action="toggle:paused"
></media-gesture>
<media-gesture class="absolute inset-0 z-0 block" event="dblpointerup" action="toggle:fullscreen"
></media-gesture>
<media-gesture
	class="absolute top-0 left-0 z-10 block h-full w-1/5"
	event="dblpointerup"
	action="seek:-10"
></media-gesture>
<media-gesture
	class="absolute top-0 right-0 z-10 block h-full w-1/5"
	event="dblpointerup"
	action="seek:10"
></media-gesture>

<!-- Buffering spinner -->
<div
	class="pointer-events-none absolute inset-0 z-10 flex items-center justify-center opacity-0 transition-opacity duration-200 media-buffering:opacity-100"
>
	<LoaderCircle class="size-12 animate-spin text-white/90 drop-shadow" />
</div>

<!-- Control bar (fades in via the media-controls state, auto-hides on idle) -->
<media-controls
	class="absolute inset-0 z-20 flex flex-col justify-end bg-gradient-to-t from-black/70 via-black/5 to-transparent opacity-0 transition-opacity duration-200 media-controls:opacity-100"
	style="--media-tooltip-y-offset: 28px; --media-menu-y-offset: 28px;"
>
	<!-- Seek slider with hover preview (thumbnail + timestamp) -->
	<media-controls-group class="flex w-full items-center px-3">
		<media-time-slider
			class="group relative inline-flex h-8 w-full cursor-pointer touch-none items-center select-none outline-none"
		>
			<div class="relative h-1.5 w-full rounded-full bg-white/20">
				<div
					class="absolute h-full rounded-full bg-white/30"
					style="width: var(--slider-progress)"
				></div>
				<div
					class="absolute h-full rounded-full bg-indigo-500"
					style="width: var(--slider-fill)"
				></div>
			</div>
			<div
				class="absolute top-1/2 z-20 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white opacity-0 shadow ring-indigo-400/40 transition-opacity group-data-[active]:opacity-100 group-data-[dragging]:ring-4"
				style="left: var(--slider-fill)"
			></div>
			<media-slider-preview
				class="pointer-events-none flex flex-col items-center opacity-0 transition-opacity duration-150 data-[visible]:opacity-100"
			>
				{#if thumbnails}
					<media-slider-thumbnail
						class="mb-1 block h-[var(--thumbnail-height)] max-h-[160px] min-h-[70px] w-[var(--thumbnail-width)] max-w-[180px] min-w-[120px] overflow-hidden rounded border border-white/20 bg-black"
						src={thumbnails}
					></media-slider-thumbnail>
				{/if}
				<media-slider-value
					class="rounded bg-zinc-900/95 px-1.5 py-0.5 text-xs font-medium text-white tabular-nums"
				></media-slider-value>
			</media-slider-preview>
		</media-time-slider>
	</media-controls-group>

	<!-- Buttons -->
	<media-controls-group class="flex w-full items-center gap-0.5 px-2 pt-0.5 pb-2">
		<Tooltip placement="top start">
			{#snippet trigger()}
				<media-play-button class={iconButton} aria-label="Play">
					<Play class="hidden size-6 media-paused:block" />
					<Pause class="size-6 media-paused:hidden" />
				</media-play-button>
			{/snippet}
			{#snippet content()}
				<span class="media-paused:hidden">Pause</span><span class="hidden media-paused:block"
					>Play</span
				>
			{/snippet}
		</Tooltip>

		<Tooltip placement="top">
			{#snippet trigger()}
				<media-mute-button class={iconButton} aria-label="Mute">
					<VolumeX class="hidden size-6 group-data-[state=muted]:block" />
					<Volume1 class="hidden size-6 group-data-[state=low]:block" />
					<Volume2 class="hidden size-6 group-data-[state=high]:block" />
				</media-mute-button>
			{/snippet}
			{#snippet content()}
				<span class="media-muted:hidden">Mute</span><span class="hidden media-muted:block"
					>Unmute</span
				>
			{/snippet}
		</Tooltip>
		<media-volume-slider
			class="group relative mr-1 ml-0.5 inline-flex h-8 w-20 cursor-pointer touch-none items-center select-none outline-none aria-hidden:hidden"
		>
			<div class="relative h-1.5 w-full rounded-full bg-white/20">
				<div class="absolute h-full rounded-full bg-white" style="width: var(--slider-fill)"></div>
			</div>
			<div
				class="absolute top-1/2 z-20 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white opacity-0 transition-opacity group-data-[active]:opacity-100"
				style="left: var(--slider-fill)"
			></div>
		</media-volume-slider>

		<div class="ml-1.5 flex items-center gap-1 text-sm font-medium text-white tabular-nums">
			<media-time type="current"></media-time>
			<span class="text-white/50">/</span>
			<media-time type="duration"></media-time>
		</div>

		<div class="flex-1"></div>

		<SettingsMenu placement="top end" />

		<Tooltip placement="top">
			{#snippet trigger()}
				<media-pip-button class={iconButton} aria-label="Picture in picture">
					<PictureInPicture2 class="size-6 media-pip:hidden" />
					<PictureInPicture class="hidden size-6 media-pip:block" />
				</media-pip-button>
			{/snippet}
			{#snippet content()}
				<span class="media-pip:hidden">Picture in picture</span><span class="hidden media-pip:block"
					>Exit picture in picture</span
				>
			{/snippet}
		</Tooltip>

		<Tooltip placement="top end">
			{#snippet trigger()}
				<media-fullscreen-button class={iconButton} aria-label="Fullscreen">
					<Maximize class="size-6 media-fullscreen:hidden" />
					<Minimize class="hidden size-6 media-fullscreen:block" />
				</media-fullscreen-button>
			{/snippet}
			{#snippet content()}
				<span class="media-fullscreen:hidden">Fullscreen</span><span
					class="hidden media-fullscreen:block">Exit fullscreen</span
				>
			{/snippet}
		</Tooltip>
	</media-controls-group>
</media-controls>
