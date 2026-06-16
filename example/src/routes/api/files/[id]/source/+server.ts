import type { RequestHandler } from '@sveltejs/kit';
import { getSource, getVideoToken } from '$lib/server/store';
import { isAuthorized } from '$lib/server/auth';

/** The worker downloads the source video from here (HTTP source). Token-guarded. */
export const GET: RequestHandler = ({ params, request }) => {
	if (!isAuthorized(request, getVideoToken(params.id!))) {
		return new Response('Unauthorized', { status: 401 });
	}
	const source = getSource(params.id!);
	if (!source) return new Response('Not found', { status: 404 });
	return new Response(new Blob([source.bytes], { type: source.contentType }), {
		headers: {
			'content-type': source.contentType,
			'content-length': String(source.bytes.byteLength),
			'cache-control': 'no-store'
		}
	});
};
