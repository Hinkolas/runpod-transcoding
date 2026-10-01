import type { RequestHandler } from '@sveltejs/kit';
import { db } from '$lib/server/db';
import { checkBucket } from '$lib/server/storage';

// Readiness probe: fails while the database or the bucket is unreachable, so a
// replica that can't serve videos takes no traffic.
export const GET: RequestHandler = async () => {
	try {
		await Promise.all([db()`select 1`, checkBucket()]);
		return new Response('ok');
	} catch (error) {
		const message = error instanceof Error ? error.message : 'not ready';
		return new Response(message, { status: 503 });
	}
};
