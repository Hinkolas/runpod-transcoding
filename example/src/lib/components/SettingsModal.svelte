<script lang="ts">
	import { untrack } from 'svelte';
	import type { Rendition, ThumbnailSettings, TranscodeSettings } from '$lib/types';
	import { DEFAULT_SETTINGS, DEFAULT_THUMBNAILS, PRESETS, blankRendition } from '$lib/presets';

	let {
		open = false,
		settings,
		onsave,
		onclose
	}: {
		open?: boolean;
		settings: TranscodeSettings;
		onsave: (settings: TranscodeSettings) => void;
		onclose: () => void;
	} = $props();

	const clone = (value: TranscodeSettings): TranscodeSettings =>
		JSON.parse(JSON.stringify(value));
	const modeOf = (s: TranscodeSettings): 'crf' | 'bitrate' =>
		s.renditions[0]?.crf != null ? 'crf' : 'bitrate';

	// Scrub-thumbnail working state. `thumbs` always carries both density values
	// so toggling interval/count keeps the inputs populated; save() emits exactly
	// one. `targetCount` defaults to 50 since DEFAULT_THUMBNAILS leaves it null.
	type ThumbDraft = {
		intervalSeconds: number;
		targetCount: number;
		width: number;
		columns: number;
		rows: number;
		format: 'jpeg' | 'webp';
		quality: number;
	};
	const thumbsModeOf = (s: TranscodeSettings): 'interval' | 'count' =>
		s.thumbnails?.targetCount != null ? 'count' : 'interval';
	const thumbDraftFrom = (s: TranscodeSettings): ThumbDraft => ({
		intervalSeconds: s.thumbnails?.intervalSeconds ?? DEFAULT_THUMBNAILS.intervalSeconds ?? 5,
		targetCount: s.thumbnails?.targetCount ?? 50,
		width: s.thumbnails?.width ?? DEFAULT_THUMBNAILS.width,
		columns: s.thumbnails?.columns ?? DEFAULT_THUMBNAILS.columns,
		rows: s.thumbnails?.rows ?? DEFAULT_THUMBNAILS.rows,
		format: s.thumbnails?.format ?? DEFAULT_THUMBNAILS.format,
		quality: s.thumbnails?.quality ?? DEFAULT_THUMBNAILS.quality
	});

	let draft = $state<TranscodeSettings>(untrack(() => clone(settings)));
	let mode = $state<'crf' | 'bitrate'>(untrack(() => modeOf(settings)));
	let preset = $state<string>(untrack(() => settings.renditions[0]?.preset ?? 'medium'));
	let thumbsEnabled = $state<boolean>(untrack(() => settings.thumbnails != null));
	let thumbsMode = $state<'interval' | 'count'>(untrack(() => thumbsModeOf(settings)));
	let thumbs = $state<ThumbDraft>(untrack(() => thumbDraftFrom(settings)));
	let lastOpen = false;

	// Reload the draft from the live settings each time the modal is opened.
	$effect(() => {
		if (open && !lastOpen) {
			draft = clone(settings);
			mode = modeOf(settings);
			preset = settings.renditions[0]?.preset ?? 'medium';
			thumbsEnabled = settings.thumbnails != null;
			thumbsMode = thumbsModeOf(settings);
			thumbs = thumbDraftFrom(settings);
		}
		lastOpen = open;
	});

	const field =
		'w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-base text-zinc-100 focus:border-indigo-500 focus:ring-0 focus:outline-none';
	const cols = 'grid-cols-[1.2fr_0.9fr_1fr_1fr_1fr_1fr_1fr_auto]';

	// Double an ffmpeg bitrate string ("5000k" -> "10000k") for a default bufsize.
	function doubleRate(value: string): string {
		const match = value.trim().toLowerCase().match(/^([\d.]+)\s*([km]?)$/);
		return match ? `${parseFloat(match[1]) * 2}${match[2]}` : value;
	}

	function setMode(next: 'crf' | 'bitrate') {
		mode = next;
		// Backfill the field the chosen mode needs so inputs aren't blank. For ABR,
		// derive the target from the rung's own cap so it never exceeds maxrate.
		for (const r of draft.renditions) {
			if (next === 'crf' && r.crf == null) r.crf = 21;
			if (next === 'bitrate' && !r.videoBitrate) r.videoBitrate = r.maxrate || '2800k';
		}
		draft.renditions = [...draft.renditions];
	}
	function addRendition() {
		draft.renditions = [...draft.renditions, { ...blankRendition(), preset }];
	}
	function removeRendition(index: number) {
		draft.renditions = draft.renditions.filter((_, i) => i !== index);
	}
	function reset() {
		draft = clone(DEFAULT_SETTINGS);
		mode = modeOf(draft);
		preset = draft.renditions[0]?.preset ?? 'medium';
		thumbsEnabled = DEFAULT_SETTINGS.thumbnails != null;
		thumbsMode = thumbsModeOf(DEFAULT_SETTINGS);
		thumbs = thumbDraftFrom(DEFAULT_SETTINGS);
	}
	function save() {
		// Emit exactly one rate-control field per rendition + the shared preset.
		// The maxrate/bufsize cap is optional; include it only when maxrate is set
		// (deriving bufsize if the user left it blank) so the pair is always valid.
		const renditions: Rendition[] = draft.renditions.map((r) => {
			const base: Rendition = {
				label: r.label,
				height: r.height,
				codec: r.codec,
				audioBitrate: r.audioBitrate,
				preset
			};
			if (mode === 'crf') base.crf = Number(r.crf);
			else base.videoBitrate = r.videoBitrate;
			if (r.maxrate) {
				base.maxrate = r.maxrate;
				base.bufsize = r.bufsize || doubleRate(r.maxrate);
			}
			return base;
		});
		// Emit the thumbnails block with exactly one density field set (or null when
		// disabled, which tells the worker to skip storyboard generation).
		const thumbnails: ThumbnailSettings | null = thumbsEnabled
			? {
					width: Number(thumbs.width),
					columns: Number(thumbs.columns),
					rows: Number(thumbs.rows),
					format: thumbs.format,
					quality: Number(thumbs.quality),
					...(thumbsMode === 'interval'
						? { intervalSeconds: Number(thumbs.intervalSeconds) }
						: { targetCount: Number(thumbs.targetCount) })
				}
			: null;
		onsave({ ...draft, renditions, thumbnails });
		onclose();
	}
</script>

{#if open}
	<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
		onclick={(e) => {
			if (e.target === e.currentTarget) onclose();
		}}
	>
		<div
			class="flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-900 shadow-2xl"
		>
			<header class="flex items-center justify-between border-b border-zinc-800 px-6 py-5">
				<div>
					<h2 class="text-xl font-semibold text-zinc-100">Encoding settings</h2>
					<p class="mt-0.5 text-sm text-zinc-500">Sent with each job to the transcoding worker</p>
				</div>
				<button
					type="button"
					onclick={onclose}
					class="rounded-md p-1.5 text-zinc-400 transition hover:bg-zinc-800 hover:text-zinc-200"
					aria-label="Close"
				>
					<svg class="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
						<path d="m6 6 12 12M18 6 6 18" stroke-linecap="round" />
					</svg>
				</button>
			</header>

			<div class="flex-1 space-y-8 overflow-y-auto px-6 py-6">
				<!-- Renditions -->
				<section>
					<div class="mb-3 flex flex-wrap items-center justify-between gap-3">
						<div class="flex flex-wrap items-center gap-3">
							<h3 class="text-base font-medium text-zinc-200">Quality ladder</h3>
							<div class="inline-flex rounded-lg border border-zinc-700 p-0.5">
								<button
									type="button"
									onclick={() => setMode('crf')}
									class="rounded-md px-3 py-1 text-sm transition {mode === 'crf'
										? 'bg-indigo-600 text-white'
										: 'text-zinc-400 hover:text-zinc-200'}"
								>
									Constant quality
								</button>
								<button
									type="button"
									onclick={() => setMode('bitrate')}
									class="rounded-md px-3 py-1 text-sm transition {mode === 'bitrate'
										? 'bg-indigo-600 text-white'
										: 'text-zinc-400 hover:text-zinc-200'}"
								>
									Target bitrate
								</button>
							</div>
						</div>
						<button
							type="button"
							onclick={addRendition}
							class="rounded-lg border border-zinc-700 px-3 py-1.5 text-sm text-zinc-300 transition hover:border-indigo-500 hover:text-indigo-300"
						>
							+ Add rendition
						</button>
					</div>

					<div class="space-y-3">
						<div class="grid {cols} gap-3 px-1 text-xs tracking-wide text-zinc-500 uppercase">
							<span>Label</span>
							<span>Height</span>
							<span>{mode === 'crf' ? 'CRF' : 'Video br'}</span>
							<span>Max rate</span>
							<span>Buf size</span>
							<span>Codec</span>
							<span>Audio br</span>
							<span></span>
						</div>

						{#each draft.renditions as rendition, index (index)}
							<div class="grid {cols} items-center gap-3">
								<input class={field} placeholder="720p" bind:value={rendition.label} />
								<input class={field} type="number" min="1" bind:value={rendition.height} />
								{#if mode === 'crf'}
									<input class={field} type="number" min="0" max="51" placeholder="21" bind:value={rendition.crf} />
								{:else}
									<input class={field} placeholder="2800k" bind:value={rendition.videoBitrate} />
								{/if}
								<input class={field} placeholder="2996k" bind:value={rendition.maxrate} />
								<input class={field} placeholder="4200k" bind:value={rendition.bufsize} />
								<select class={field} bind:value={rendition.codec}>
									<option value="h264">h264</option>
									<option value="h265">h265</option>
								</select>
								<input class={field} placeholder="128k" bind:value={rendition.audioBitrate} />
								<button
									type="button"
									onclick={() => removeRendition(index)}
									disabled={draft.renditions.length <= 1}
									class="rounded-md p-2 text-zinc-500 transition hover:text-rose-400 disabled:opacity-30"
									aria-label="Remove rendition"
								>
									<svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
										<path d="M5 12h14" stroke-linecap="round" />
									</svg>
								</button>
							</div>
						{/each}
					</div>

					<p class="mt-2 px-1 text-xs text-zinc-500">
						{mode === 'crf'
							? 'Constant quality — lower CRF = higher quality + bigger files (~21 is the streaming sweet spot). Max rate / Buf size are an optional peak cap (recommended for streaming).'
							: 'Target bitrate — fixed average bitrate per rung. Max rate / Buf size are an optional cap; clear both for uncapped ABR (and keep max rate ≥ video bitrate when set).'}
					</p>
				</section>

				<!-- Global options -->
				<section class="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
					<label class="block">
						<span class="mb-1.5 block text-sm text-zinc-400">Preset</span>
						<select class={field} bind:value={preset}>
							{#each PRESETS as p (p)}
								<option value={p}>{p}</option>
							{/each}
						</select>
					</label>
					<label class="block">
						<span class="mb-1.5 block text-sm text-zinc-400">Segment length (s)</span>
						<input class={field} type="number" min="2" max="20" bind:value={draft.segmentSeconds} />
					</label>
					<label class="block">
						<span class="mb-1.5 block text-sm text-zinc-400">Threads (0 = auto)</span>
						<input class={field} type="number" min="0" bind:value={draft.threads} />
					</label>
					<label class="flex items-center gap-2.5 pt-7">
						<input
							type="checkbox"
							bind:checked={draft.allowUpscale}
							class="h-5 w-5 rounded border-zinc-600 bg-zinc-950 text-indigo-500 focus:ring-0"
						/>
						<span class="text-base text-zinc-300">Allow upscaling</span>
					</label>
				</section>

				<!-- Scrub thumbnails -->
				<section>
					<div class="mb-3 flex flex-wrap items-center justify-between gap-3">
						<div>
							<h3 class="text-base font-medium text-zinc-200">Scrub thumbnails</h3>
							<p class="mt-0.5 text-sm text-zinc-500">
								Seek-bar preview images — storyboard sprites + WebVTT
							</p>
						</div>
						<button
							type="button"
							role="switch"
							aria-checked={thumbsEnabled}
							onclick={() => (thumbsEnabled = !thumbsEnabled)}
							aria-label="Generate scrub thumbnails"
							class="relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition {thumbsEnabled
								? 'bg-indigo-600'
								: 'bg-zinc-700'}"
						>
							<span
								class="inline-block h-5 w-5 transform rounded-full bg-white shadow transition {thumbsEnabled
									? 'translate-x-[1.375rem]'
									: 'translate-x-0.5'}"
							></span>
						</button>
					</div>

					{#if thumbsEnabled}
						<div class="mb-3 inline-flex rounded-lg border border-zinc-700 p-0.5">
							<button
								type="button"
								onclick={() => (thumbsMode = 'interval')}
								class="rounded-md px-3 py-1 text-sm transition {thumbsMode === 'interval'
									? 'bg-indigo-600 text-white'
									: 'text-zinc-400 hover:text-zinc-200'}"
							>
								Every N seconds
							</button>
							<button
								type="button"
								onclick={() => (thumbsMode = 'count')}
								class="rounded-md px-3 py-1 text-sm transition {thumbsMode === 'count'
									? 'bg-indigo-600 text-white'
									: 'text-zinc-400 hover:text-zinc-200'}"
							>
								Total count
							</button>
						</div>

						<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
							{#if thumbsMode === 'interval'}
								<label class="block">
									<span class="mb-1.5 block text-sm text-zinc-400">Interval (seconds)</span>
									<input class={field} type="number" min="0.5" step="0.5" bind:value={thumbs.intervalSeconds} />
								</label>
							{:else}
								<label class="block">
									<span class="mb-1.5 block text-sm text-zinc-400">Total thumbnails</span>
									<input class={field} type="number" min="1" bind:value={thumbs.targetCount} />
								</label>
							{/if}
							<label class="block">
								<span class="mb-1.5 block text-sm text-zinc-400">Thumb width (px)</span>
								<input class={field} type="number" min="16" bind:value={thumbs.width} />
							</label>
							<label class="block">
								<span class="mb-1.5 block text-sm text-zinc-400">Format</span>
								<select class={field} bind:value={thumbs.format}>
									<option value="jpeg">JPEG</option>
									<option value="webp">WebP</option>
								</select>
							</label>
							<label class="block">
								<span class="mb-1.5 block text-sm text-zinc-400">Sprite columns</span>
								<input class={field} type="number" min="1" bind:value={thumbs.columns} />
							</label>
							<label class="block">
								<span class="mb-1.5 block text-sm text-zinc-400">Sprite rows</span>
								<input class={field} type="number" min="1" bind:value={thumbs.rows} />
							</label>
							<label class="block">
								<span class="mb-1.5 block text-sm text-zinc-400">Quality (1–100)</span>
								<input class={field} type="number" min="1" max="100" bind:value={thumbs.quality} />
							</label>
						</div>

						<p class="mt-2 px-1 text-xs text-zinc-500">
							{thumbsMode === 'interval'
								? 'One thumbnail every N seconds — finer intervals scrub more smoothly but make larger sprites.'
								: 'Roughly this many thumbnails spread across the whole video, regardless of its length.'}
							Packed {thumbs.columns}×{thumbs.rows} per sprite sheet. WebP is smaller; JPEG is maximally
							compatible. Higher quality = sharper previews and bigger files.
						</p>
					{:else}
						<p class="px-1 text-sm text-zinc-500">
							Disabled — no storyboard is generated and the player shows a plain seek bar.
						</p>
					{/if}
				</section>
			</div>

			<footer class="flex items-center justify-between gap-3 border-t border-zinc-800 px-6 py-5">
				<button
					type="button"
					onclick={reset}
					class="text-sm text-zinc-400 transition hover:text-zinc-200"
				>
					Reset to defaults
				</button>
				<div class="flex gap-3">
					<button
						type="button"
						onclick={onclose}
						class="rounded-lg border border-zinc-700 px-5 py-2 text-sm text-zinc-300 transition hover:bg-zinc-800"
					>
						Cancel
					</button>
					<button
						type="button"
						onclick={save}
						class="rounded-lg bg-indigo-600 px-5 py-2 text-sm font-medium text-white transition hover:bg-indigo-500"
					>
						Save settings
					</button>
				</div>
			</footer>
		</div>
	</div>
{/if}
