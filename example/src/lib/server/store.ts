// In-memory state. No database — videos, their source bytes, the HLS output
// files the worker uploads, and a per-job auth token all live in RAM. State
// is held on `globalThis` so it survives Vite HMR reloads during development.
//
// This is intentionally simple for a demo: everything is lost on restart, and
// large files are buffered in memory.

import type {
	ProbeInfo,
	RenditionOutput,
	TranscodeSettings,
	VideoRecord,
	VideoStatus
} from '$lib/types';

export type StoredFile = { bytes: Uint8Array<ArrayBuffer>; contentType: string };

type InternalVideo = {
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
	executionTimeMs: number | null;
	delayTimeMs: number | null;
	metadata: Record<string, unknown> | null;
	renditionsOut: RenditionOutput[] | null;
	error: string | null;
	// Per-job bearer token: minted when a transcode is submitted, scoped to this
	// video's source/output endpoints, and revoked when the job finishes. Secret —
	// never serialized to the browser.
	token: string | null;
	source: StoredFile | null;
	outputs: Map<string, StoredFile>;
	lastPolledAt: number;
};

type Store = {
	videos: Map<string, InternalVideo>;
};

const globalRef = globalThis as unknown as { __transcodeStore?: Store };

function getStore(): Store {
	if (!globalRef.__transcodeStore) {
		globalRef.__transcodeStore = { videos: new Map() };
	}
	return globalRef.__transcodeStore;
}

const store = getStore();

/** The per-job token for a video, for validating source/output requests. */
export function getVideoToken(id: string): string | null {
	return store.videos.get(id)?.token ?? null;
}

function toPublic(video: InternalVideo): VideoRecord {
	return {
		id: video.id,
		title: video.title,
		filename: video.filename,
		sizeBytes: video.sizeBytes,
		contentType: video.contentType,
		status: video.status,
		createdAt: video.createdAt,
		updatedAt: video.updatedAt,
		settings: video.settings,
		runpodJobId: video.runpodJobId,
		probe: video.probe,
		durationMs: video.durationMs,
		executionTimeMs: video.executionTimeMs,
		delayTimeMs: video.delayTimeMs,
		metadata: video.metadata,
		renditionsOut: video.renditionsOut,
		error: video.error,
		hasMaster: video.outputs.has('master.m3u8'),
		posterReady: video.outputs.has('poster.jpg'),
		storyboardReady: video.outputs.has('storyboard.vtt')
	};
}

function stripExtension(name: string): string {
	const dot = name.lastIndexOf('.');
	return dot > 0 ? name.slice(0, dot) : name;
}

export function createVideo(input: {
	filename: string;
	contentType: string;
	bytes: Uint8Array<ArrayBuffer>;
}): VideoRecord {
	const now = Date.now();
	const video: InternalVideo = {
		id: crypto.randomUUID(),
		title: stripExtension(input.filename) || 'Untitled',
		filename: input.filename,
		sizeBytes: input.bytes.byteLength,
		contentType: input.contentType || 'video/mp4',
		status: 'idle',
		createdAt: now,
		updatedAt: now,
		settings: null,
		runpodJobId: null,
		probe: null,
		durationMs: null,
		executionTimeMs: null,
		delayTimeMs: null,
		metadata: null,
		renditionsOut: null,
		error: null,
		token: null,
		source: { bytes: input.bytes, contentType: input.contentType || 'video/mp4' },
		outputs: new Map(),
		lastPolledAt: 0
	};
	store.videos.set(video.id, video);
	return toPublic(video);
}

export function listVideos(): VideoRecord[] {
	return [...store.videos.values()]
		.sort((a, b) => b.createdAt - a.createdAt)
		.map(toPublic);
}

export function getVideo(id: string): VideoRecord | null {
	const video = store.videos.get(id);
	return video ? toPublic(video) : null;
}

export function deleteVideo(id: string): boolean {
	return store.videos.delete(id);
}

type VideoMutation = Partial<
	Pick<
		InternalVideo,
		| 'status'
		| 'settings'
		| 'runpodJobId'
		| 'probe'
		| 'durationMs'
		| 'executionTimeMs'
		| 'delayTimeMs'
		| 'metadata'
		| 'renditionsOut'
		| 'error'
		| 'token'
		| 'lastPolledAt'
	>
>;

export function updateVideo(id: string, changes: VideoMutation): VideoRecord | null {
	const video = store.videos.get(id);
	if (!video) return null;
	Object.assign(video, changes);
	video.updatedAt = Date.now();
	return toPublic(video);
}

/** Wipe any previously uploaded HLS output (used when re-encoding). */
export function clearOutputs(id: string): void {
	store.videos.get(id)?.outputs.clear();
}

export function getSource(id: string): StoredFile | null {
	return store.videos.get(id)?.source ?? null;
}

export function putOutput(id: string, relativePath: string, file: StoredFile): boolean {
	const video = store.videos.get(id);
	if (!video) return false;
	video.outputs.set(relativePath, file);
	video.updatedAt = Date.now();
	return true;
}

export function getOutput(id: string, relativePath: string): StoredFile | null {
	return store.videos.get(id)?.outputs.get(relativePath) ?? null;
}

/**
 * Records that still have an in-flight RunPod job and are due for a status poll.
 * `lastPolledAt` throttling lives in the caller via {@link markPolled}.
 */
export function pollableVideos(throttleMs: number): VideoRecord[] {
	const now = Date.now();
	return [...store.videos.values()]
		.filter(
			(v) =>
				v.runpodJobId !== null &&
				(v.status === 'queued' || v.status === 'processing') &&
				now - v.lastPolledAt >= throttleMs
		)
		.map(toPublic);
}

/** Internal accessor for services that need the job id without exposing bytes. */
export function getJobId(id: string): string | null {
	return store.videos.get(id)?.runpodJobId ?? null;
}
