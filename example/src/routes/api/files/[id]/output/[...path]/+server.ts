import type { RequestHandler } from '@sveltejs/kit';
import { getOutput, getVideo, getVideoToken, putOutput } from '$lib/server/store';
import { isAuthorized } from '$lib/server/auth';

function contentTypeFor(path: string, fallback: string): string {
	if (path.endsWith('.m3u8')) return 'application/vnd.apple.mpegurl';
	if (path.endsWith('.ts')) return 'video/mp2t';
	if (path.endsWith('.jpg') || path.endsWith('.jpeg')) return 'image/jpeg';
	if (path.endsWith('.webp')) return 'image/webp'; // WebP storyboard sprites
	if (path.endsWith('.vtt')) return 'text/vtt'; // scrub-thumbnail index
	return fallback || 'application/octet-stream';
}

/** The worker PUTs each HLS output file here (HTTP destination). Token-guarded. */
export const PUT: RequestHandler = async ({ params, request }) => {
	if (!isAuthorized(request, getVideoToken(params.id!))) {
		return new Response('Unauthorized', { status: 401 });
	}
	const id = params.id!;
	if (!getVideo(id)) return new Response('Not found', { status: 404 });
	const path = params.path!; // e.g. "720p/segment_00001.ts" or "master.m3u8"
	const bytes = new Uint8Array(await request.arrayBuffer());
	putOutput(id, path, { bytes, contentType: contentTypeFor(path, request.headers.get('content-type') ?? '') });
	return new Response(null, { status: 204 });
};

/** The browser's player fetches playlists/segments/poster from here (public). */
export const GET: RequestHandler = ({ params }) => {
	const file = getOutput(params.id!, params.path!);
	if (!file) return new Response('Not found', { status: 404 });
	return new Response(new Blob([file.bytes], { type: file.contentType }), {
		headers: { 'content-type': file.contentType, 'cache-control': 'no-store' }
	});
};
