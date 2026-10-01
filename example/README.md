# Transcoding Demo UI

A small SvelteKit app that drives the [`worker/`](../worker) RunPod transcoder using its
**HTTP transport** mode. Upload a video, tweak the encoding settings in a modal, kick off a job, and
play the resulting HLS stream. Video records and job status live in **PostgreSQL**, the uploaded source and
the generated HLS files in an **S3 bucket**, so the app can run as several replicas and survives restarts.
[`skali.yaml`](skali.yaml) provisions both.

## How it works

This server is both the worker's HTTP **source** and HTTP **destination**:

```
Browser ──upload──▶ SvelteKit ──▶ Postgres (records) + S3 bucket (files)
                       │  submit job (RunPod API)
                       ▼
                    RunPod worker
                       │  GET  /api/files/:id/source         (downloads the source)
                       │  PUT  /api/files/:id/output/<path>  (uploads each HLS file)
                       ▼
Browser ◀──play HLS── SvelteKit serves /api/files/:id/output/master.m3u8
```

- **Upload** (`POST /api/videos`) streams the source video into the bucket and inserts its record.
- **Encode** (`POST /api/videos/:id/transcode`) submits a RunPod job whose `source`/`destination` are
  `http` URLs pointing back here, with a freshly minted **per-job bearer token** (scoped to that one video,
  revoked when the job finishes) in the request headers.
- The worker downloads the source, encodes, and PUTs every output file (playlists, segments, poster,
  and — when enabled — the scrub-thumbnail storyboard) back to `/api/files/:id/output/...`, which we
  store in the bucket. The bucket stays private: the worker and the player always go through this app.
- The browser polls `GET /api/videos` while jobs are in flight; the server lazily polls RunPod's job
  status and serves the HLS output to a [Vidstack](https://vidstack.io) player — adaptive playback via
  [hls.js](https://github.com/video-dev/hls.js). The player is a **custom control bar** built from
  Vidstack's UI primitives (`src/lib/components/player/`) with [Lucide](https://lucide.dev) icons and
  Tailwind styling: play/pause, volume, a seek bar with storyboard thumbnail previews, a quality menu
  (from the HLS renditions) + playback speed, picture-in-picture, and fullscreen.

> ⚠️ **Public reachability.** RunPod's cloud workers must be able to reach this server to download the
> source and upload the output. Set `PUBLIC_BASE_URL` to a public URL. For local development, expose
> the dev server with a tunnel (e.g. `cloudflared tunnel --url http://localhost:5173`) and point
> `PUBLIC_BASE_URL` at the tunnel URL.

### Scrub thumbnails

The encoding-settings modal has an optional **Scrub thumbnails** section (on by default). When enabled,
the worker generates a storyboard — sprite sheets plus a `storyboard.vtt` index — alongside the HLS
renditions, and the [Vidstack](https://vidstack.io) player shows preview images as you drag the seek
bar. You control the density (a thumbnail every _N_ seconds **or** a target count), per-thumbnail width,
the sprite grid (columns × rows per sheet), format (JPEG or WebP) and quality. Turn it off and no
storyboard is generated — the player simply falls back to a plain seek bar.

## Configuration

Environment variables (all read at runtime):

| Var                                            | Required    | Purpose                                                                                             |
| ---------------------------------------------- | ----------- | --------------------------------------------------------------------------------------------------- |
| `RUNPOD_API_KEY`                               | yes         | RunPod API key used to submit/poll jobs.                                                            |
| `RUNPOD_ENDPOINT_ID`                           | yes         | The serverless endpoint running `worker/`.                                                          |
| `PUBLIC_BASE_URL`                              | recommended | Publicly reachable base URL of _this_ server (no trailing slash). Falls back to the request origin. |
| `APP_ID`                                       | optional    | Tags each job's `metadata.app` for usage attribution (default `demo-ui`).                           |
| `DATABASE_URL`                                 | yes         | PostgreSQL connection URL. The schema is created by `scripts/migrate.js`.                           |
| `S3_ENDPOINT`, `S3_BUCKET`, `S3_REGION`        | yes         | S3-compatible bucket for sources and HLS output (path-style addressing).                            |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY`               | yes         | Bucket credentials.                                                                                 |
| `BODY_SIZE_LIMIT`                              | prod        | adapter-node request body cap. The Dockerfile sets `Infinity` so large uploads work.                |
| `RUNPOD_EXECUTION_TIMEOUT_MS`, `RUNPOD_TTL_MS` | optional    | Job policy overrides.                                                                               |

## Usage tracking

Each job is tagged with `metadata: { app: APP_ID }` and the worker echoes it back. When a job completes,
this server captures RunPod's `executionTime` (the **billed** compute ms) and `delayTime` (queue ms) from the
job status and stores them on the video record (shown as "Compute (billed)" and "App" in the UI). To bill an
app, sum `executionTimeMs` grouped by `metadata.app` over the period — no cost logic lives here; that's yours.

## Develop & deploy with skali

[`skali.yaml`](skali.yaml) declares the app, a Postgres database and a private bucket, and wires the
database and bucket env vars from their outputs. The schema migration runs as the release command before
each version goes live; `/health/ready` keeps a replica out of rotation while the database or bucket is
unreachable. Supply these values per environment (in a `.env` here, or stored with the first deploy):

| Value                | Required | Purpose                                                                                   |
| -------------------- | -------- | ----------------------------------------------------------------------------------------- |
| `APP_DOMAIN`         | yes      | The route's domain, e.g. `transcoding.localhost` for local dev. `ORIGIN` derives from it. |
| `RUNPOD_API_KEY`     | yes      | As above.                                                                                 |
| `RUNPOD_ENDPOINT_ID` | yes      | As above.                                                                                 |
| `PUBLIC_BASE_URL`    | local    | Defaults to `https://$APP_DOMAIN`, which is right for a deployed environment.             |
| `APP_ID`             | optional | As above (default `demo-ui`).                                                             |

```sh
bun install
skali dev                # local cluster with Postgres + bucket; runs `bun run dev` on port 5173
skali dev run migrate    # create the schema (the release command doesn't run for the host dev server)
skali dev --preview      # local cluster, built from the Dockerfile exactly like a remote deploy
skali deploy             # deploy to the current remote
```

RunPod's workers can't reach `*.localhost`, so to encode locally expose the dev server with a tunnel
(e.g. `cloudflared tunnel --url http://localhost:5173`) and set `PUBLIC_BASE_URL` to the tunnel URL.

## Build & run with Docker

Outside skali, point the image at your own Postgres and S3-compatible bucket and create the schema once:

```sh
docker build -t transcoding-demo .
docker run --rm -e DATABASE_URL=... transcoding-demo node scripts/migrate.js
docker run --rm -p 3000:3000 \
  -e RUNPOD_API_KEY=... \
  -e RUNPOD_ENDPOINT_ID=... \
  -e PUBLIC_BASE_URL=https://your-public-host \
  -e DATABASE_URL=... \
  -e S3_ENDPOINT=... -e S3_BUCKET=... -e S3_REGION=... \
  -e S3_ACCESS_KEY=... -e S3_SECRET_KEY=... \
  transcoding-demo
```

## Notes & limitations (it's a demo)

- Uploads are capped at 2 GiB. Sources stream into the bucket; each HLS output file is buffered once on its
  way in.
- Output GET endpoints are unauthenticated (random UUID paths) so the browser's player can fetch them;
  the source GET and all output PUTs require the job's per-job bearer token (scoped to that one video,
  revoked once the job completes).
- Encoding settings are stored per-browser in `localStorage`.
