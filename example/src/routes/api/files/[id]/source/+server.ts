import type { RequestHandler } from '@sveltejs/kit';
import { getSource, getVideoToken } from '$lib/server/store';
import { isAuthorized } from '$lib/server/auth';

/** The worker downloads the source video from here (HTTP source). Token-guarded. */
export const GET: RequestHandler = async ({ params, request }) => {
	if (!isAuthorized(request, await getVideoToken(params.id!))) {
		return new Response('Unauthorized', { status: 401 });
	}
	const source = await getSource(params.id!);
	if (!source) return new Response('Not found', { status: 404 });
	const headers: Record<string, string> = {
		'content-type': source.contentType,
		'cache-control': 'no-store'
	};
	if (source.contentLength !== undefined) headers['content-length'] = String(source.contentLength);
	return new Response(source.body, { headers });
};
