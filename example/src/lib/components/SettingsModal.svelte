<script lang="ts">
	import { untrack } from 'svelte';
	import type { Rendition, TranscodeSettings } from '$lib/types';
	import { DEFAULT_SETTINGS, PRESETS, blankRendition } from '$lib/presets';

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

	let draft = $state<TranscodeSettings>(untrack(() => clone(settings)));
	let mode = $state<'crf' | 'bitrate'>(untrack(() => modeOf(settings)));
	let preset = $state<string>(untrack(() => settings.renditions[0]?.preset ?? 'medium'));
	let lastOpen = false;

	// Reload the draft from the live settings each time the modal is opened.
	$effect(() => {
		if (open && !lastOpen) {
			draft = clone(settings);
			mode = modeOf(settings);
			preset = settings.renditions[0]?.preset ?? 'medium';
		}
		lastOpen = open;
	});

	const field =
		'w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-base text-zinc-100 focus:border-indigo-500 focus:ring-0 focus:outline-none';
	const cols = 'grid-cols-[1.2fr_0.9fr_1fr_1fr_1fr_1fr_1fr_auto]';

	function setMode(next: 'crf' | 'bitrate') {
		mode = next;
		// Backfill the field the chosen mode needs so inputs aren't blank.
		for (const r of draft.renditions) {
			if (next === 'crf' && r.crf == null) r.crf = 21;
			if (next === 'bitrate' && !r.videoBitrate) r.videoBitrate = '2800k';
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
	}
	function save() {
		// Emit exactly one rate-control field per rendition + the shared preset.
		const renditions: Rendition[] = draft.renditions.map((r) => {
			const base = {
				label: r.label,
				height: r.height,
				maxrate: r.maxrate,
				bufsize: r.bufsize,
				codec: r.codec,
				audioBitrate: r.audioBitrate,
				preset
			};
			return mode === 'crf'
				? { ...base, crf: Number(r.crf) }
				: { ...base, videoBitrate: r.videoBitrate };
		});
		onsave({ ...draft, renditions });
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
							? 'Constant quality — lower CRF = higher quality + bigger files (~21 is the streaming sweet spot). Max rate caps the peak bitrate.'
							: 'Target bitrate — fixed average bitrate per rung, capped by max rate.'}
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
