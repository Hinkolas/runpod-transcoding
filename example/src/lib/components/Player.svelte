<script lang="ts">
	import { onMount } from 'svelte';
	import type { VideoRecord } from '$lib/types';

	// Vidstack default-layout styles (SSR-safe — Vite extracts these to CSS).
	import 'vidstack/player/styles/default/theme.css';
	import 'vidstack/player/styles/default/layouts/video.css';

	let { video }: { video: VideoRecord | null } = $props();

	// The custom elements touch `window`/`customElements`, so register them only
	// on the client. Until `registered` flips true we render the placeholder, which
	// also guarantees the element is upgraded before Svelte sets its properties.
	let registered = $state(false);
	let player = $state<HTMLElement | null>(null);

	onMount(async () => {
		await import('vidstack/player'); // <media-player>, <media-provider>, core
		await import('vidstack/player/ui'); // controls, buttons, gestures, sliders, menus
		await import('vidstack/player/layouts/default'); // <media-video-layout>
		registered = true;
	});

	const playable = $derived(!!video && video.status === 'ready' && video.hasMaster);
	const src = $derived(playable && video ? `/api/files/${video.id}/output/master.m3u8` : '');
	// Frame shown before playback, when the worker extracted one.
	const poster = $derived(
		playable && video?.posterReady ? `/api/files/${video.id}/output/poster.jpg` : ''
	);
	// Scrub-thumbnail index — only when the worker produced one. The VTT references
	// its sprites by relative name, so they resolve next to it under this route.
	const thumbnails = $derived(
		playable && video?.storyboardReady ? `/api/files/${video.id}/output/storyboard.vtt` : ''
	);

	// Use the locally bundled hls.js instead of Vidstack's default CDN load, so the
	// demo works offline and pins the version the app already depends on.
	type HlsProvider = { type?: string; library: () => Promise<unknown> };
	function onProviderChange(event: Event) {
		const provider = (event as CustomEvent<HlsProvider>).detail;
		if (provider?.type === 'hls') {
			provider.library = () => import('hls.js');
		}
	}

	$effect(() => {
		const el = player;
		if (!el) return;
		el.addEventListener('provider-change', onProviderChange);
		return () => el.removeEventListener('provider-change', onProviderChange);
	});
</script>

<div class="overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-900/40">
	{#if playable && registered}
		<media-player
			bind:this={player}
			class="w-full"
			style="aspect-ratio: 16 / 9; --media-brand: #6366f1; --media-focus-ring-color: #818cf8;"
			{src}
			poster={poster || undefined}
			title={video?.title ?? ''}
			playsInline
		>
			<media-provider></media-provider>
			<media-video-layout thumbnails={thumbnails || null}></media-video-layout>
		</media-player>
	{:else}
		<div class="relative aspect-video bg-black">
			<div class="absolute inset-0 flex flex-col items-center justify-center gap-3 text-zinc-500">
				{#if playable && !registered}
					<svg
						class="h-7 w-7 animate-spin text-indigo-400"
						viewBox="0 0 24 24"
						fill="none"
						aria-hidden="true"
					>
						<circle
							class="opacity-25"
							cx="12"
							cy="12"
							r="10"
							stroke="currentColor"
							stroke-width="3"
						/>
						<path
							class="opacity-90"
							fill="currentColor"
							d="M4 12a8 8 0 0 1 8-8v3a5 5 0 0 0-5 5H4z"
						/>
					</svg>
					<p class="text-base">Loading player…</p>
				{:else}
					<svg
						class="h-12 w-12"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="1.3"
						aria-hidden="true"
					>
						<rect x="3" y="5" width="18" height="14" rx="2" />
						<path d="m10 9 5 3-5 3z" fill="currentColor" stroke="none" />
					</svg>
					<p class="text-base">
						{#if video && (video.status === 'queued' || video.status === 'processing')}
							Encoding your renditions…
						{:else if video && video.status === 'error'}
							This video failed to process
						{:else if video}
							Encode this video to preview it here
						{:else}
							Select or upload a video to begin
						{/if}
					</p>
				{/if}
			</div>
		</div>
	{/if}
</div>
