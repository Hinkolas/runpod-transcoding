import type { Rendition, TranscodeSettings } from '$lib/types';

/** Sensible default ladder: 480p / 720p / 1080p, H.264 + AAC — plays everywhere.
 * Defaults to CRF (constant quality) with a maxrate cap + medium preset, the
 * recommended rate control for web VOD. */
export const DEFAULT_RENDITIONS: Rendition[] = [
	{ label: '480p', height: 480, crf: 22, maxrate: '1400k', bufsize: '2800k', codec: 'h264', audioBitrate: '128k', preset: 'medium' },
	{ label: '720p', height: 720, crf: 21, maxrate: '2800k', bufsize: '5600k', codec: 'h264', audioBitrate: '128k', preset: 'medium' },
	{ label: '1080p', height: 1080, crf: 21, maxrate: '5000k', bufsize: '10000k', codec: 'h264', audioBitrate: '192k', preset: 'medium' }
];

export const DEFAULT_SETTINGS: TranscodeSettings = {
	renditions: DEFAULT_RENDITIONS,
	segmentSeconds: 6,
	allowUpscale: false,
	threads: 0
};

/** A fresh rendition row carrying both rate-control fields so toggling modes works. */
export function blankRendition(): Rendition {
	return {
		label: '',
		height: 720,
		crf: 21,
		videoBitrate: '2800k',
		maxrate: '2996k',
		bufsize: '5600k',
		codec: 'h264',
		audioBitrate: '128k',
		preset: 'medium'
	};
}

export const PRESETS = ['veryfast', 'faster', 'fast', 'medium', 'slow', 'slower'] as const;
