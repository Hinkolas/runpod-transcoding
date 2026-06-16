import type { Rendition, TranscodeSettings } from '$lib/types';

/** Sensible default ladder: 480p / 720p / 1080p, H.264 + AAC — plays everywhere. */
export const DEFAULT_RENDITIONS: Rendition[] = [
	{ label: '480p', height: 480, videoBitrate: '1400k', maxrate: '1498k', bufsize: '2100k', codec: 'h264', audioBitrate: '128k' },
	{ label: '720p', height: 720, videoBitrate: '2800k', maxrate: '2996k', bufsize: '4200k', codec: 'h264', audioBitrate: '128k' },
	{ label: '1080p', height: 1080, videoBitrate: '5000k', maxrate: '5350k', bufsize: '7500k', codec: 'h264', audioBitrate: '192k' }
];

export const DEFAULT_SETTINGS: TranscodeSettings = {
	renditions: DEFAULT_RENDITIONS,
	segmentSeconds: 6,
	allowUpscale: false,
	threads: 0
};

export function blankRendition(): Rendition {
	return { label: '', height: 720, videoBitrate: '2800k', maxrate: '2996k', bufsize: '4200k', codec: 'h264', audioBitrate: '128k' };
}
