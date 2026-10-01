// Creates the schema. Runs as skali's release command before a new version goes
// live (and via `skali dev run migrate` while iterating locally). Idempotent.
import postgres from 'postgres';

if (!process.env.DATABASE_URL) throw new Error('DATABASE_URL is not set');
const sql = postgres(process.env.DATABASE_URL, { onnotice: () => {} });

await sql`
	create table if not exists videos (
		id text primary key,
		title text not null,
		filename text not null,
		size_bytes bigint not null,
		content_type text not null,
		status text not null,
		created_at bigint not null,
		updated_at bigint not null,
		settings jsonb,
		runpod_job_id text,
		probe jsonb,
		duration_ms bigint,
		execution_time_ms bigint,
		delay_time_ms bigint,
		metadata jsonb,
		renditions_out jsonb,
		progress jsonb,
		error text,
		token text,
		has_master boolean not null default false,
		poster_ready boolean not null default false,
		storyboard_ready boolean not null default false,
		last_polled_at bigint not null default 0
	)`;
await sql`create index if not exists videos_created_at_idx on videos (created_at desc)`;

await sql.end();
console.log('migrations applied');
