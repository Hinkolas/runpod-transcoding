<script lang="ts">
	import type { MenuPlacement } from 'vidstack';
	import Settings from '@lucide/svelte/icons/settings';
	import ChevronLeft from '@lucide/svelte/icons/chevron-left';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Tooltip from './Tooltip.svelte';

	let { placement = 'top end' }: { placement?: MenuPlacement } = $props();

	// Shared classes for a submenu trigger row and a radio option row.
	const submenuButton =
		'group flex w-full cursor-pointer items-center rounded-md px-2.5 py-2 text-left text-white outline-none ring-indigo-400 select-none data-[hocus]:bg-white/10 data-[focus]:ring-2 data-[open]:sticky data-[open]:top-0 data-[open]:z-10 data-[open]:bg-zinc-900 aria-hidden:hidden';
	const radioRow =
		'group flex w-full cursor-pointer items-center rounded-md px-2.5 py-2 text-white outline-none ring-indigo-400 select-none data-[hocus]:bg-white/10 data-[focus]:ring-2';
	// A ring that fills into an indigo dot when the option is selected.
	const radioDot =
		'size-3.5 shrink-0 rounded-full border-2 border-white/40 transition-all group-data-[checked]:border-[5px] group-data-[checked]:border-indigo-400';
</script>

<media-menu class="relative inline-flex">
	<Tooltip placement="top">
		{#snippet trigger()}
			<media-menu-button
				class="group inline-flex size-10 cursor-pointer items-center justify-center rounded-md text-white outline-none ring-indigo-400 transition hover:bg-white/15 data-[focus]:ring-2 aria-hidden:hidden"
				aria-label="Settings"
			>
				<Settings
					class="size-6 transition-transform duration-200 ease-out group-data-[open]:rotate-45"
				/>
			</media-menu-button>
		{/snippet}
		{#snippet content()}Settings{/snippet}
	</Tooltip>

	<media-menu-items
		{placement}
		class="flex max-h-[280px] min-w-[240px] flex-col overflow-y-auto overscroll-contain rounded-lg border border-zinc-700 bg-zinc-900/95 p-1.5 text-[15px] font-medium text-white opacity-0 shadow-2xl backdrop-blur-sm transition-opacity duration-150 outline-none data-[open]:opacity-100"
	>
		<!-- Quality (auto-populated from the HLS renditions; hidden when there's only one) -->
		<media-menu>
			<media-menu-button class={submenuButton}>
				<ChevronLeft class="mr-1 hidden size-4 group-data-[open]:block" />
				<span>Quality</span>
				<span class="ml-auto text-sm text-white/50" data-part="hint"></span>
				<ChevronRight class="ml-1 size-4 text-white/50 group-data-[open]:hidden" />
			</media-menu-button>
			<media-menu-items class="hidden flex-col py-1 data-[open]:flex">
				<media-quality-radio-group class="flex flex-col">
					<template>
						<media-radio class={radioRow}>
							<div class={radioDot}></div>
							<span class="ml-2.5" data-part="label"></span>
							<span class="ml-auto text-xs text-white/40 tabular-nums" data-part="bitrate"></span>
						</media-radio>
					</template>
				</media-quality-radio-group>
			</media-menu-items>
		</media-menu>

		<!-- Playback speed -->
		<media-menu>
			<media-menu-button class={submenuButton}>
				<ChevronLeft class="mr-1 hidden size-4 group-data-[open]:block" />
				<span>Speed</span>
				<span class="ml-auto text-sm text-white/50" data-part="hint"></span>
				<ChevronRight class="ml-1 size-4 text-white/50 group-data-[open]:hidden" />
			</media-menu-button>
			<media-menu-items class="hidden flex-col py-1 data-[open]:flex">
				<media-speed-radio-group class="flex flex-col" rates={[0.5, 0.75, 1, 1.25, 1.5, 1.75, 2]}>
					<template>
						<media-radio class={radioRow}>
							<div class={radioDot}></div>
							<span class="ml-2.5" data-part="label"></span>
						</media-radio>
					</template>
				</media-speed-radio-group>
			</media-menu-items>
		</media-menu>
	</media-menu-items>
</media-menu>
