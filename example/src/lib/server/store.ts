// Video records live in PostgreSQL; the source video and the HLS output files
// the worker uploads live in the S3 bucket (see storage.ts). Nothing is held in
// process memory, so any replica can serve any request — including the worker's
// source download and output uploads — and state survives restarts and deploys.

import type { JSONValue } from 'postgres';
import type { VideoRecord } from '$lib/types';
import { db } from '$lib/server/db';
import {
	deletePrefix,
	getObject,
	outputKey,
	outputPrefix,
	putObject,
	sourceKey,
	uploadStream,
	videoPrefix,
	type StoredObject
} from '$lib/server/storage';

type VideoRow = VideoRecord & {
	// Per-job bearer token: minted when a transcode is submitted, scoped to this
	// video's source/output endpoints, and revoked when the job finishes. Secret —
	// never serialized to the browser.
	token: string | null;
	lastPolledAt: number;
};

// Columns the browser may see: everything except the token and poll bookkeeping.
const PUBLIC_COLUMNS = [
	'id',
	'title',
	'filename',
	'size_bytes',
	'content_type',
	'status',
	'created_at',
	'updated_at',
	'settings',
	'runpod_job_id',
	'probe',
	'duration_ms',
	'execution_time_ms',
	'delay_time_ms',
	'metadata',
	'renditions_out',
	'progress',
	'error',
	'has_master',
	'poster_ready',
	'storyboard_ready'
];

// jsonb columns must be sent with sql.json, or arrays would bind as Postgres arrays.
const JSON_FIELDS = new Set(['settings', 'probe', 'metadata', 'renditionsOut', 'progress']);

// Output files whose arrival the UI tracks.
const READY_FLAGS: Record<string, 'hasMaster' | 'posterReady' | 'storyboardReady'> = {
	'master.m3u8': 'hasMaster',
	'poster.jpg': 'posterReady',
	'storyboard.vtt': 'storyboardReady'
};

/** The per-job token for a video, for validating source/output requests. */
export async function getVideoToken(id: string): Promise<string | null> {
	const sql = db();
	const [row] = await sql<{ token: string | null }[]>`
		select token from videos where id = ${id}`;
	return row?.token ?? null;
}

function stripExtension(name: string): string {
	const dot = name.lastIndexOf('.');
	return dot > 0 ? name.slice(0, dot) : name;
}

/**
 * Store an uploaded source video: stream the body into the bucket, then insert
 * the record. Throws if the body exceeds `maxBytes`.
 */
export async function createVideo(input: {
	filename: string;
	contentType: string;
	body: ReadableStream<Uint8Array>;
	maxBytes: number;
}): Promise<VideoRecord> {
	const sql = db();
	const id = crypto.randomUUID();
	const contentType = input.contentType || 'video/mp4';
	let sizeBytes: number;
	try {
		sizeBytes = await uploadStream(sourceKey(id), input.body, contentType, input.maxBytes);
	} catch (error) {
		await deletePrefix(videoPrefix(id)).catch(() => {});
		throw error;
	}
	const now = Date.now();
	const [video] = await sql<VideoRecord[]>`
		insert into videos ${sql({
			id,
			title: stripExtension(input.filename) || 'Untitled',
			filename: input.filename,
			sizeBytes,
			contentType,
			status: 'idle',
			createdAt: now,
			updatedAt: now
		})}
		returning ${sql(PUBLIC_COLUMNS)}`;
	return video;
}

export async function listVideos(): Promise<VideoRecord[]> {
	const sql = db();
	return await sql<VideoRecord[]>`
		select ${sql(PUBLIC_COLUMNS)} from videos order by created_at desc`;
}

export async function getVideo(id: string): Promise<VideoRecord | null> {
	const sql = db();
	const [video] = await sql<VideoRecord[]>`
		select ${sql(PUBLIC_COLUMNS)} from videos where id = ${id}`;
	return video ?? null;
}

export async function deleteVideo(id: string): Promise<boolean> {
	const sql = db();
	const removed = await sql`delete from videos where id = ${id}`;
	if (!removed.count) return false;
	await deletePrefix(videoPrefix(id));
	return true;
}

type VideoMutation = Partial<
	Pick<
		VideoRow,
		| 'status'
		| 'settings'
		| 'runpodJobId'
		| 'probe'
		| 'durationMs'
		| 'executionTimeMs'
		| 'delayTimeMs'
		| 'metadata'
		| 'renditionsOut'
		| 'progress'
		| 'error'
		| 'token'
		| 'lastPolledAt'
		| 'hasMaster'
		| 'posterReady'
		| 'storyboardReady'
	>
>;

export async function updateVideo(id: string, changes: VideoMutation): Promise<VideoRecord | null> {
	const sql = db();
	const values = Object.fromEntries(
		Object.entries(changes).map(([field, value]) => [
			field,
			JSON_FIELDS.has(field) && value !== null ? sql.json(value as JSONValue) : value
		])
	);
	const [video] = await sql<VideoRecord[]>`
		update videos set ${sql({ ...values, updatedAt: Date.now() })}
		where id = ${id}
		returning ${sql(PUBLIC_COLUMNS)}`;
	return video ?? null;
}

/** Wipe any previously uploaded HLS output (used when re-encoding). */
export async function clearOutputs(id: string): Promise<void> {
	await deletePrefix(outputPrefix(id));
	await updateVideo(id, { hasMaster: false, posterReady: false, storyboardReady: false });
}

export async function getSource(id: string): Promise<StoredObject | null> {
	return getObject(sourceKey(id));
}

export async function putOutput(
	id: string,
	relativePath: string,
	file: { bytes: Uint8Array; contentType: string }
): Promise<void> {
	await putObject(outputKey(id, relativePath), file.bytes, file.contentType);
	const flag = READY_FLAGS[relativePath];
	if (flag) await updateVideo(id, { [flag]: true });
}

export async function getOutput(id: string, relativePath: string): Promise<StoredObject | null> {
	return getObject(outputKey(id, relativePath));
}

/**
 * Claim the records that still have an in-flight RunPod job and are due for a
 * status poll. Claiming stamps `last_polled_at` in the same statement, so
 * concurrent requests (on any replica) don't stampede RunPod for the same job.
 */
export async function pollableVideos(throttleMs: number): Promise<VideoRecord[]> {
	const sql = db();
	const now = Date.now();
	return await sql<VideoRecord[]>`
		update videos set last_polled_at = ${now}
		where runpod_job_id is not null
			and status in ('queued', 'processing')
			and last_polled_at <= ${now - throttleMs}
		returning ${sql(PUBLIC_COLUMNS)}`;
}
