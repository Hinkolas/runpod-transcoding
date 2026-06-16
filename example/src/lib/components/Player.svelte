<script lang="ts">
	import { onDestroy } from 'svelte';
	import type Hls from 'hls.js';
	import type { VideoRecord } from '$lib/types';
	import { formatClock } from '$lib/format';

	let { video }: { video: VideoRecord | null } = $props();

	let videoEl = $state<HTMLVideoElement | null>(null);
	let hls: Hls | null = null;
	let levels = $state<{ index: number; label: string }[]>([]);
	let currentLevel = $state(-1);
	let isNative = $state(false);
	let currentTime = $state(0);
	let duration = $state(0);
	let playError = $state('');
	let loadedKey = '';

	const playable = $derived(!!video && video.status === 'ready' && video.hasMaster);
	const src = $derived(playable && video ? `/api/files/${video.id}/output/master.m3u8` : '');

	function teardown() {
		hls?.destroy();
		hls = null;
		levels = [];
		currentLevel = -1;
		isNative = false;
		playError = '';
		currentTime = 0;
		duration = 0;
	}

	async function setup(source: string) {
		if (!videoEl) return;
		teardown();
		const HlsCtor = (await import('hls.js')).default;

		if (HlsCtor.isSupported()) {
			const instance = new HlsCtor({ enableWorker: true });
			instance.loadSource(source);
			instance.attachMedia(videoEl);
			instance.on(HlsCtor.Events.MANIFEST_PARSED, () => {
				levels = instance.levels
					.map((lvl, index) => ({
						index,
						height: lvl.height ?? 0,
						label: lvl.height ? `${lvl.height}p` : `${Math.round((lvl.bitrate || 0) / 1000)}k`
					}))
					.sort((a, b) => b.height - a.height)
					.map(({ index, label }) => ({ index, label }));
				currentLevel = instance.autoLevelEnabled ? -1 : instance.currentLevel;
			});
			instance.on(HlsCtor.Events.LEVEL_SWITCHED, (_event, data) => {
				currentLevel = instance.autoLevelEnabled ? -1 : data.level;
			});
			instance.on(HlsCtor.Events.ERROR, (_event, data) => {
				if (data.fatal) playError = `Playback error: ${data.details}`;
			});
			hls = instance;
			isNative = false;
		} else if (videoEl.canPlayType('application/vnd.apple.mpegurl')) {
			videoEl.src = source;
			isNative = true;
		} else {
			playError = 'HLS playback is not supported in this browser.';
		}
	}

	function selectLevel(index: number) {
		if (!hls) return;
		hls.currentLevel = index; // -1 = auto
		currentLevel = index;
	}

	$effect(() => {
		const key = src && video ? `${video.id}:${src}` : '';
		if (key && key !== loadedKey && videoEl) {
			loadedKey = key;
			void setup(src);
		} else if (!key && loadedKey) {
			loadedKey = '';
			videoEl?.removeAttribute('src');
			teardown();
		}
	});

	onDestroy(teardown);
</script>

<div class="overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-900/40">
	<div class="relative aspect-video bg-black">
		<!-- svelte-ignore a11y_media_has_caption -->
		<video
			bind:this={videoEl}
			class="absolute inset-0 h-full w-full object-contain {playable ? '' : 'opacity-0'}"
			controls
			playsinline
			ontimeupdate={(e) => (currentTime = e.currentTarget.currentTime)}
			onloadedmetadata={(e) => (duration = e.currentTarget.duration)}
		></video>

		{#if !playable}
			<div class="absolute inset-0 flex flex-col items-center justify-center gap-3 text-zinc-500">
				<svg class="h-12 w-12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3" aria-hidden="true">
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
			</div>
		{/if}
	</div>

	<div class="flex items-center justify-between gap-3 px-4 py-3">
		<div class="font-mono text-sm text-zinc-400">
			{#if playable}
				<span class="text-indigo-300">{formatClock(currentTime)}</span>
				<span class="text-zinc-600"> / </span>
				<span>{formatClock(duration)}</span>
			{:else}
				<span class="text-zinc-600">—:—— / —:——</span>
			{/if}
		</div>

		{#if playable && !isNative && levels.length > 1}
			<div class="flex items-center gap-1">
				<button
					type="button"
					onclick={() => selectLevel(-1)}
					class="rounded-md px-2.5 py-1 text-sm transition {currentLevel === -1
						? 'bg-indigo-600 text-white'
						: 'text-zinc-400 hover:bg-zinc-800'}"
				>
					Auto
				</button>
				{#each levels as level (level.index)}
					<button
						type="button"
						onclick={() => selectLevel(level.index)}
						class="rounded-md px-2.5 py-1 text-sm transition {currentLevel === level.index
							? 'bg-indigo-600 text-white'
							: 'text-zinc-400 hover:bg-zinc-800'}"
					>
						{level.label}
					</button>
				{/each}
			</div>
		{:else if playable && isNative}
			<span class="text-sm text-zinc-500">Adaptive (native HLS)</span>
		{/if}
	</div>

	{#if playError}
		<p class="border-t border-zinc-800 px-4 py-2 font-mono text-sm text-rose-400">{playError}</p>
	{/if}
</div>
