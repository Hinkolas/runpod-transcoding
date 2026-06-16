// Shared types used by both the browser and the server. Keep free of server-only
// imports so it can be imported anywhere.

export type VideoStatus = 'idle' | 'queued' | 'processing' | 'ready' | 'error';

export type VideoCodec = 'h264' | 'h265';

/** One output quality level — mirrors the worker's `Rendition`. */
export type Rendition = {
	label: string;
	height: number;
	videoBitrate: string;
	maxrate: string;
	bufsize: string;
	codec: VideoCodec;
	audioBitrate: string;
};

/** Everything the user can tweak in the settings modal before encoding. */
export type TranscodeSettings = {
	renditions: Rendition[];
	segmentSeconds: number;
	allowUpscale: boolean;
	threads: number;
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
	durationMs: number | null;
	renditionsOut: RenditionOutput[] | null;
	error: string | null;
	hasMaster: boolean;
	posterReady: boolean;
};
