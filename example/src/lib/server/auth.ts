import { workerToken } from '$lib/server/store';

/** Validates the Bearer token the worker sends on source GET / output PUT. */
export function isAuthorized(request: Request): boolean {
	const header = request.headers.get('authorization') ?? '';
	return header === `Bearer ${workerToken()}`;
}
