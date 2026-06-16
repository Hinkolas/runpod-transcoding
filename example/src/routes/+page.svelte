<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import type { PageData } from './$types';
	import type { TranscodeSettings, VideoRecord } from '$lib/types';
	import { DEFAULT_SETTINGS } from '$lib/presets';
	import { formatDuration } from '$lib/format';
	import Player from '$lib/components/Player.svelte';
	import UploadDrop from '$lib/components/UploadDrop.svelte';
	import VideoCard from '$lib/components/VideoCard.svelte';
	import SettingsModal from '$lib/components/SettingsModal.svelte';

	let { data }: { data: PageData } = $props();

	// Seed once from the SSR load; this component then owns the list.
	let videos = $state<VideoRecord[]>(untrack(() => data.videos));
	let selectedId = $state<string | null>(
		untrack(() => data.videos.find((v) => v.status === 'ready')?.id ?? data.videos[0]?.id ?? null)
	);
	let settings = $state<TranscodeSettings>(DEFAULT_SETTINGS);
	let showSettings = $state(false);
	let uploading = $state(false);
	let uploadError = $state('');

	const selected = $derived(videos.find((v) => v.id === selectedId) ?? null);
	const hasPending = $derived(videos.some((v) => v.status === 'queued' || v.status === 'processing'));

	const SETTINGS_KEY = 'transcode-settings';

	onMount(() => {
		const saved = localStorage.getItem(SETTINGS_KEY);
		if (saved) {
			try {
				settings = { ...DEFAULT_SETTINGS, ...JSON.parse(saved) };
			} catch {
				/* ignore corrupt settings */
			}
		}
	});

	function saveSettings(next: TranscodeSettings) {
		settings = next;
		localStorage.setItem(SETTINGS_KEY, JSON.stringify(next));
	}

	// Poll for job status only while something is in flight.
	let pollTimer: ReturnType<typeof setInterval> | null = null;
	$effect(() => {
		if (hasPending && !pollTimer) {
			pollTimer = setInterval(refreshList, 2500);
		} else if (!hasPending && pollTimer) {
			clearInterval(pollTimer);
			pollTimer = null;
		}
	});
	onDestroy(() => {
		if (pollTimer) clearInterval(pollTimer);
	});

	async function refreshList() {
		const res = await fetch('/api/videos');
		if (res.ok) videos = (await res.json()).videos;
	}

	function patch(updated: VideoRecord) {
		videos = videos.map((v) => (v.id === updated.id ? updated : v));
	}

	async function uploadFiles(files: File[]) {
		uploading = true;
		uploadError = '';
		try {
			for (const file of files) {
				const res = await fetch(`/api/videos?filename=${encodeURIComponent(file.name)}`, {
					method: 'POST',
					headers: { 'content-type': file.type || 'application/octet-stream' },
					body: file
				});
				const body = await res.json();
				if (!res.ok) {
					uploadError = body.message ?? 'Upload failed';
					continue;
				}
				videos = [body.video, ...videos];
				selectedId ??= body.video.id;
			}
		} catch {
			uploadError = 'Upload failed';
		} finally {
			uploading = false;
		}
	}

	async function encode(video: VideoRecord) {
		patch({ ...video, status: 'queued', error: null });
		const res = await fetch(`/api/videos/${video.id}/transcode`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify(settings)
		});
		const body = await res.json();
		if (body.video) patch(body.video);
		selectedId = video.id;
	}

	async function remove(video: VideoRecord) {
		await fetch(`/api/videos/${video.id}`, { method: 'DELETE' });
		videos = videos.filter((v) => v.id !== video.id);
		if (selectedId === video.id) selectedId = videos[0]?.id ?? null;
	}
</script>

<svelte:head><title>HLS Transcoder</title></svelte:head>

<div class="min-h-screen bg-zinc-950 text-zinc-100">
	<header class="sticky top-0 z-30 border-b border-zinc-800/80 bg-zinc-950/80 backdrop-blur">
		<div class="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
			<div class="flex items-center gap-3">
				<div class="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600">
					<svg class="h-5 w-5 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
						<rect x="3" y="5" width="18" height="14" rx="2" />
						<path d="m10 9 5 3-5 3z" fill="currentColor" stroke="none" />
					</svg>
				</div>
				<div>
					<h1 class="text-base font-semibold">HLS Transcoder</h1>
					<p class="text-sm text-zinc-500">RunPod worker · HTTP transport · in-memory</p>
				</div>
			</div>
			<button
				type="button"
				onclick={() => (showSettings = true)}
				class="flex items-center gap-2 rounded-lg border border-zinc-700 px-3 py-1.5 text-sm text-zinc-300 transition hover:border-zinc-500 hover:bg-zinc-900"
			>
				<svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
					<circle cx="12" cy="12" r="3" />
					<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-2.82 1.17V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 8 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 3.6 14a1.65 1.65 0 0 0-1.51-1H2a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 3.6 8a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 8 3.6h.09A1.65 1.65 0 0 0 9.6 2.09V2a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 20.4 8v.09a1.65 1.65 0 0 0 1.51 1H22a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
				</svg>
				Settings
			</button>
		</div>
	</header>

	<main class="mx-auto grid max-w-7xl gap-6 px-6 py-6 lg:grid-cols-[1fr_380px]">
		<!-- Player + details -->
		<section class="space-y-4">
			<Player video={selected} />

			{#if selected}
				<div class="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-6">
					<h2 class="truncate text-xl font-semibold" title={selected.title}>{selected.title}</h2>
					<dl class="mt-5 grid grid-cols-2 gap-4 text-base sm:grid-cols-4">
						<div>
							<dt class="text-sm text-zinc-500">Source</dt>
							<dd class="mt-1 text-zinc-200">
								{selected.probe?.width && selected.probe?.height
									? `${selected.probe.width}×${selected.probe.height}`
									: '—'}
							</dd>
						</div>
						<div>
							<dt class="text-sm text-zinc-500">Renditions</dt>
							<dd class="mt-1 text-zinc-200">{selected.renditionsOut?.length ?? '—'}</dd>
						</div>
						<div>
							<dt class="text-sm text-zinc-500">Encode time</dt>
							<dd class="mt-1 text-zinc-200">{formatDuration(selected.durationMs)}</dd>
						</div>
						<div>
							<dt class="text-sm text-zinc-500">Codec</dt>
							<dd class="mt-1 text-zinc-200">{selected.probe?.codec ?? '—'}</dd>
						</div>
					</dl>

					{#if selected.renditionsOut?.length}
						<div class="mt-5 flex flex-wrap gap-2">
							{#each selected.renditionsOut as r (r.label)}
								<span class="rounded-md bg-zinc-800 px-2.5 py-1 font-mono text-sm text-zinc-300">
									{r.label}{r.width ? ` · ${r.width}×${r.height}` : ''}
								</span>
							{/each}
						</div>
					{/if}

					{#if selected.status === 'error' && selected.error}
						<p class="mt-5 rounded-lg border border-rose-500/30 bg-rose-500/5 px-3 py-2 font-mono text-sm text-rose-300">
							{selected.error}
						</p>
					{/if}
				</div>
			{/if}
		</section>

		<!-- Library -->
		<aside class="space-y-4">
			<UploadDrop onfiles={uploadFiles} {uploading} />
			{#if uploadError}
				<p class="text-xs text-rose-400">{uploadError}</p>
			{/if}

			<div class="flex items-center justify-between px-1">
				<h2 class="text-sm font-medium tracking-wide text-zinc-500 uppercase">
					Library · {videos.length}
				</h2>
				{#if hasPending}
					<span class="flex items-center gap-1.5 text-sm text-indigo-300">
						<span class="h-2 w-2 animate-pulse rounded-full bg-current"></span>
						Working…
					</span>
				{/if}
			</div>

			<div class="space-y-2.5">
				{#each videos as video (video.id)}
					<VideoCard
						{video}
						selected={video.id === selectedId}
						onselect={(v) => (selectedId = v.id)}
						onencode={encode}
						ondelete={remove}
					/>
				{:else}
					<p class="rounded-xl border border-dashed border-zinc-800 px-4 py-8 text-center text-base text-zinc-600">
						No videos yet. Upload one to get started.
					</p>
				{/each}
			</div>
		</aside>
	</main>
</div>

<SettingsModal
	open={showSettings}
	{settings}
	onsave={saveSettings}
	onclose={() => (showSettings = false)}
/>
