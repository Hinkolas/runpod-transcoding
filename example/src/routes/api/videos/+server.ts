import { json, type RequestHandler } from '@sveltejs/kit';
import { createVideo, listVideos, pollableVideos } from '$lib/server/store';
import { refreshJob } from '$lib/server/runpod';
import { UploadTooLargeError } from '$lib/server/storage';

const POLL_THROTTLE_MS = 1500;
const MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024; // 2 GiB safety cap

/** List videos. Lazily refreshes any in-flight RunPod jobs (throttled). */
export const GET: RequestHandler = async () => {
	const pending = await pollableVideos(POLL_THROTTLE_MS);
	if (pending.length) await Promise.all(pending.map((video) => refreshJob(video)));
	return json({ videos: await listVideos() });
};

/**
 * Upload a source video. The body is the raw file bytes (Content-Type =
 * the video MIME), with the name in the `filename` query param. Sending it as a
 * non-form content type keeps it clear of SvelteKit's CSRF form check. Streamed
 * straight into the bucket.
 */
export const POST: RequestHandler = async ({ request, url }) => {
	const contentLength = Number(request.headers.get('content-length') || 0);
	if (contentLength > MAX_UPLOAD_BYTES) {
		return json({ message: 'File exceeds the 2 GiB demo limit' }, { status: 413 });
	}
	if (!request.body || request.headers.get('content-length') === '0') {
		return json({ message: 'Empty upload' }, { status: 400 });
	}
	try {
		const video = await createVideo({
			filename: url.searchParams.get('filename') || 'upload.mp4',
			contentType: request.headers.get('content-type') || 'video/mp4',
			body: request.body,
			maxBytes: MAX_UPLOAD_BYTES
		});
		return json({ video }, { status: 201 });
	} catch (error) {
		if (error instanceof UploadTooLargeError) {
			return json({ message: 'File exceeds the 2 GiB demo limit' }, { status: 413 });
		}
		const message = error instanceof Error ? error.message : 'Upload failed';
		return json({ message }, { status: 500 });
	}
};
