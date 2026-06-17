<script lang="ts">
	let {
		onfiles,
		uploading = false,
		progress = 0
	}: { onfiles: (files: File[]) => void; uploading?: boolean; progress?: number } = $props();

	let dragging = $state(false);
	let input = $state<HTMLInputElement | null>(null);

	function handleDrop(event: DragEvent) {
		event.preventDefault();
		dragging = false;
		const files = [...(event.dataTransfer?.files ?? [])].filter((f) => f.type.startsWith('video/'));
		if (files.length) onfiles(files);
	}

	function handlePick(event: Event) {
		const target = event.currentTarget as HTMLInputElement;
		if (target.files?.length) onfiles([...target.files]);
		target.value = '';
	}
</script>

<button
	type="button"
	class="group flex w-full flex-col items-center gap-2 rounded-2xl border border-dashed px-4 py-7 text-center transition
		{dragging
		? 'border-indigo-400 bg-indigo-500/5'
		: 'border-zinc-700 hover:border-zinc-500 hover:bg-zinc-900/50'}"
	ondragover={(e) => {
		e.preventDefault();
		dragging = true;
	}}
	ondragleave={() => (dragging = false)}
	ondrop={handleDrop}
	onclick={() => input?.click()}
	disabled={uploading}
>
	{#if uploading}
		<svg class="h-7 w-7 animate-spin text-indigo-400" viewBox="0 0 24 24" fill="none" aria-hidden="true">
			<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" />
			<path class="opacity-90" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v3a5 5 0 0 0-5 5H4z" />
		</svg>
		<span class="text-base text-zinc-300">Uploading… {Math.round(progress)}%</span>
		<div
			class="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-zinc-800"
			role="progressbar"
			aria-valuenow={Math.round(progress)}
			aria-valuemin="0"
			aria-valuemax="100"
		>
			<div
				class="h-full rounded-full bg-indigo-400 transition-all duration-200 ease-out"
				style="width: {progress}%"
			></div>
		</div>
	{:else}
		<svg class="h-7 w-7 text-zinc-500 transition group-hover:text-indigo-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
			<path d="M12 16V4m0 0 4 4m-4-4-4 4" stroke-linecap="round" stroke-linejoin="round" />
			<path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" stroke-linecap="round" />
		</svg>
		<div>
			<p class="text-base font-medium text-zinc-200">Drop a video or click to upload</p>
			<p class="mt-1 text-sm text-zinc-500">MP4, MOV, WebM — stored in server memory</p>
		</div>
	{/if}
</button>

<input bind:this={input} type="file" accept="video/*" multiple class="hidden" onchange={handlePick} />
