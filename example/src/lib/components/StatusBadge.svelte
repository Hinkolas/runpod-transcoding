<script lang="ts">
	import type { VideoStatus } from '$lib/types';

	let { status }: { status: VideoStatus } = $props();

	const config: Record<VideoStatus, { label: string; classes: string; pulse?: boolean }> = {
		idle: { label: 'Ready to encode', classes: 'bg-zinc-800 text-zinc-300 ring-zinc-700' },
		queued: { label: 'Queued', classes: 'bg-amber-500/10 text-amber-300 ring-amber-500/30', pulse: true },
		processing: { label: 'Encoding', classes: 'bg-indigo-500/10 text-indigo-300 ring-indigo-500/30', pulse: true },
		ready: { label: 'Ready', classes: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30' },
		error: { label: 'Failed', classes: 'bg-rose-500/10 text-rose-300 ring-rose-500/30' }
	};

	const current = $derived(config[status]);
</script>

<span
	class="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-medium ring-1 ring-inset {current.classes}"
>
	<span
		class="h-2 w-2 rounded-full bg-current"
		class:animate-pulse={current.pulse}
	></span>
	{current.label}
</span>
