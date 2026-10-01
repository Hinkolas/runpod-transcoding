// PostgreSQL connection for video records. The schema lives in
// scripts/migrate.js, which runs as the deployment's release command.

import { env } from '$env/dynamic/private';
import postgres from 'postgres';

const globalRef = globalThis as unknown as { __transcodeSql?: postgres.Sql };

function connect(): postgres.Sql {
	if (!env.DATABASE_URL) throw new Error('DATABASE_URL is not set');
	return postgres(env.DATABASE_URL, {
		// snake_case columns ⇄ camelCase fields, so rows map straight onto VideoRecord.
		transform: postgres.camel,
		// Timestamps and durations are epoch/ms numbers; parse bigint as a JS number.
		types: {
			bigint: { to: 20, from: [20], serialize: (n: number) => String(n), parse: Number }
		}
	});
}

/** The shared client, created on first use and kept on `globalThis` across Vite HMR reloads. */
export function db(): postgres.Sql {
	return (globalRef.__transcodeSql ??= connect());
}
