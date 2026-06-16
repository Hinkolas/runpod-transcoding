/** Validates the per-job Bearer token the worker sends on source GET / output PUT.
 * `expectedToken` is the token stored on the specific video being accessed. */
export function isAuthorized(request: Request, expectedToken: string | null): boolean {
	if (!expectedToken) return false;
	const header = request.headers.get('authorization') ?? '';
	return header === `Bearer ${expectedToken}`;
}
