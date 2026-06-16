import { json, type RequestHandler } from '@sveltejs/kit';
import { createVideo, listVideos, pollableVideos } from '$lib/server/store';
import { refreshJob } from '$lib/server/runpod';

const POLL_THROTTLE_MS = 1500;
const MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024; // 2 GiB safety cap

/** List videos. Lazily refreshes any in-flight RunPod jobs (throttled). */
export const GET: RequestHandler = async () => {
	const pending = pollableVideos(POLL_THROTTLE_MS);
	if (pending.length) await Promise.all(pending.map((video) => refreshJob(video)));
	return json({ videos: listVideos() });
};

/**
 * Upload a source video. The body is the raw file bytes (Content-Type =
 * the video MIME), with the name in the `filename` query param. Sending it as a
 * non-form content type keeps it clear of SvelteKit's CSRF form check. Buffered
 * in memory.
 */
export const POST: RequestHandler = async ({ request, url }) => {
	const contentLength = Number(request.headers.get('content-length') || 0);
	if (contentLength > MAX_UPLOAD_BYTES) {
		return json({ message: 'File exceeds the 2 GiB demo limit' }, { status: 413 });
	}
	const bytes = new Uint8Array(await request.arrayBuffer());
	if (!bytes.byteLength) {
		return json({ message: 'Empty upload' }, { status: 400 });
	}
	if (bytes.byteLength > MAX_UPLOAD_BYTES) {
		return json({ message: 'File exceeds the 2 GiB demo limit' }, { status: 413 });
	}
	const video = createVideo({
		filename: url.searchParams.get('filename') || 'upload.mp4',
		contentType: request.headers.get('content-type') || 'video/mp4',
		bytes
	});
	return json({ video }, { status: 201 });
};
