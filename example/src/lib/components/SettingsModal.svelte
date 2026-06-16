<script lang="ts">
	import { untrack } from 'svelte';
	import type { TranscodeSettings } from '$lib/types';
	import { DEFAULT_SETTINGS, blankRendition } from '$lib/presets';

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

	let draft = $state<TranscodeSettings>(untrack(() => clone(settings)));
	let lastOpen = false;

	// Reload the draft from the live settings each time the modal is opened.
	$effect(() => {
		if (open && !lastOpen) draft = clone(settings);
		lastOpen = open;
	});

	const field =
		'w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-base text-zinc-100 focus:border-indigo-500 focus:ring-0 focus:outline-none';
	const cols = 'grid-cols-[1.2fr_0.9fr_1fr_1fr_1fr_1fr_1fr_auto]';

	function addRendition() {
		draft.renditions = [...draft.renditions, blankRendition()];
	}
	function removeRendition(index: number) {
		draft.renditions = draft.renditions.filter((_, i) => i !== index);
	}
	function reset() {
		draft = clone(DEFAULT_SETTINGS);
	}
	function save() {
		onsave(clone(draft));
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
					<div class="mb-3 flex items-center justify-between">
						<h3 class="text-base font-medium text-zinc-200">Quality ladder</h3>
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
							<span>Video br</span>
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
								<input class={field} placeholder="2800k" bind:value={rendition.videoBitrate} />
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
				</section>

				<!-- Global options -->
				<section class="grid gap-5 sm:grid-cols-3">
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
