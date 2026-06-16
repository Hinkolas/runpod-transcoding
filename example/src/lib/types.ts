// Shared types used by both the browser and the server. Keep free of server-only
// imports so it can be imported anywhere.

export type VideoStatus = 'idle' | 'queued' | 'processing' | 'ready' | 'error';

export type VideoCodec = 'h264' | 'h265';

/** One output quality level — mirrors the worker's `Rendition`.
 * Set exactly one of `crf` (constant quality) or `videoBitrate` (ABR). */
export type Rendition = {
	label: string;
	height: number;
	crf?: number | null;
	videoBitrate?: string | null;
	maxrate?: string | null; // optional peak cap (pair with bufsize)
	bufsize?: string | null;
	codec: VideoCodec;
	audioBitrate: string;
	preset?: string;
};

/** Scrub-thumbnail (storyboard) settings — mirrors the worker's `Thumbnails`.
 * Set exactly one of `intervalSeconds` (one thumb every N seconds) or
 * `targetCount` (~this many thumbs total). `null` on a video's settings means
 * the feature is off and no storyboard is generated. */
export type ThumbnailSettings = {
	intervalSeconds?: number | null;
	targetCount?: number | null;
	width: number; // per-thumb width; height derives from the source aspect ratio
	columns: number; // tiles per sprite-sheet row
	rows: number; // tiles per sprite-sheet column (columns*rows per sheet)
	format: 'jpeg' | 'webp';
	quality: number; // 1–100, higher = better
};

/** Everything the user can tweak in the settings modal before encoding. */
export type TranscodeSettings = {
	renditions: Rendition[];
	segmentSeconds: number;
	allowUpscale: boolean;
	threads: number;
	thumbnails: ThumbnailSettings | null; // null = scrub thumbnails disabled
};

export type ProbeInfo = {
	width?: number | null;
	height?: number | null;
	durationSeconds?: number | null;
	codec?: string | null;
	format?: string | null;
	bitrate?: number | null;
};

export type RenditionOutput = {
	label: string;
	height: number;
	width: number | null;
	bandwidth: number;
};

/** Client-facing view of a video (no file bytes). */
export type VideoRecord = {
	id: string;
	title: string;
	filename: string;
	sizeBytes: number;
	contentType: string;
	status: VideoStatus;
	createdAt: number;
	updatedAt: number;
	settings: TranscodeSettings | null;
	runpodJobId: string | null;
	probe: ProbeInfo | null;
	durationMs: number | null; // worker-measured encode wall time
	executionTimeMs: number | null; // RunPod billed compute time (the usage number)
	delayTimeMs: number | null; // RunPod queue wait (not billed)
	metadata: Record<string, unknown> | null; // echoed job tags, e.g. { app }
	renditionsOut: RenditionOutput[] | null;
	error: string | null;
	hasMaster: boolean;
	posterReady: boolean;
	storyboardReady: boolean; // scrub-thumbnail VTT was produced and uploaded
};
