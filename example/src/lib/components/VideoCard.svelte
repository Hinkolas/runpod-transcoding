<script lang="ts">
	import type { VideoRecord } from '$lib/types';
	import { formatBytes } from '$lib/format';
	import StatusBadge from './StatusBadge.svelte';

	let {
		video,
		selected = false,
		onselect,
		onencode,
		ondelete
	}: {
		video: VideoRecord;
		selected?: boolean;
		onselect: (video: VideoRecord) => void;
		onencode: (video: VideoRecord) => void;
		ondelete: (video: VideoRecord) => void;
	} = $props();

	const busy = $derived(video.status === 'queued' || video.status === 'processing');
	const posterUrl = $derived(video.posterReady ? `/api/files/${video.id}/output/poster.jpg` : null);
</script>

<div
	class="group rounded-xl border p-3 transition {selected
		? 'border-indigo-500/60 bg-indigo-500/5'
		: 'border-zinc-800 bg-zinc-900/40 hover:border-zinc-700'}"
>
	<div class="flex gap-3">
		<button
			type="button"
			onclick={() => onselect(video)}
			class="relative aspect-video w-28 shrink-0 overflow-hidden rounded-lg bg-black ring-1 ring-zinc-800"
			aria-label="Select {video.title}"
		>
			{#if posterUrl}
				<img src={posterUrl} alt="" class="h-full w-full object-cover" />
			{:else}
				<div class="flex h-full w-full items-center justify-center text-zinc-600">
					<svg class="h-6 w-6" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
						<path d="M8 5v14l11-7z" />
					</svg>
				</div>
			{/if}
		</button>

		<div class="min-w-0 flex-1">
			<button type="button" onclick={() => onselect(video)} class="block w-full text-left">
				<p class="truncate text-base font-medium text-zinc-100" title={video.title}>{video.title}</p>
			</button>
			<p class="mt-0.5 text-sm text-zinc-500">{formatBytes(video.sizeBytes)}</p>
			<div class="mt-2"><StatusBadge status={video.status} /></div>
			{#if video.status === 'error' && video.error}
				<p class="mt-1.5 line-clamp-2 text-sm text-rose-400/80" title={video.error}>{video.error}</p>
			{/if}
		</div>
	</div>

	<div class="mt-3 flex items-center gap-2">
		<button
			type="button"
			onclick={() => onencode(video)}
			disabled={busy}
			class="flex-1 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40"
		>
			{video.status === 'idle' ? 'Encode' : busy ? 'Encoding…' : 'Re-encode'}
		</button>
		<button
			type="button"
			onclick={() => ondelete(video)}
			disabled={busy}
			class="rounded-lg border border-zinc-700 px-3 py-2 text-sm text-zinc-400 transition hover:border-rose-500/50 hover:text-rose-400 disabled:opacity-40"
			aria-label="Delete {video.title}"
		>
			<svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
				<path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m-9 0 1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13" stroke-linecap="round" stroke-linejoin="round" />
			</svg>
		</button>
	</div>
</div>
