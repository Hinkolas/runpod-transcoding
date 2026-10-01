import type { RequestHandler } from '@sveltejs/kit';

// Liveness probe: the process can serve requests.
export const GET: RequestHandler = () => new Response('ok');
