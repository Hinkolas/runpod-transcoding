# Transcoding Demo UI

A small SvelteKit app that drives the [`worker/`](../worker) RunPod transcoder using its
**HTTP transport** mode. Upload a video, tweak the encoding settings in a modal, kick off a job, and
play the resulting HLS stream — all with **no database**: videos, job status, and the generated HLS
files live in server memory.

## How it works

This server is both the worker's HTTP **source** and HTTP **destination**:

```
Browser ──upload──▶ SvelteKit (in-memory)
                       │  submit job (RunPod API)
                       ▼
                    RunPod worker
                       │  GET  /api/files/:id/source         (downloads the source)
                       │  PUT  /api/files/:id/output/<path>  (uploads each HLS file)
                       ▼
Browser ◀──play HLS── SvelteKit serves /api/files/:id/output/master.m3u8
```

- **Upload** (`POST /api/videos`) buffers the source video in memory.
- **Encode** (`POST /api/videos/:id/transcode`) submits a RunPod job whose `source`/`destination` are
  `http` URLs pointing back here, signed with a per-process bearer token.
- The worker downloads the source, encodes, and PUTs every output file (playlists, segments, poster)
  back to `/api/files/:id/output/...`, which we store in memory.
- The browser polls `GET /api/videos` while jobs are in flight; the server lazily polls RunPod's job
  status and serves the HLS output to an [hls.js](https://github.com/video-dev/hls.js) player.

> ⚠️ **Public reachability.** RunPod's cloud workers must be able to reach this server to download the
> source and upload the output. Set `PUBLIC_BASE_URL` to a public URL. For local development, expose
> the dev server with a tunnel (e.g. `cloudflared tunnel --url http://localhost:5173`) and point
> `PUBLIC_BASE_URL` at the tunnel URL.

## Configuration

Environment variables (all read at runtime):

| Var | Required | Purpose |
|-----|----------|---------|
| `RUNPOD_API_KEY` | yes | RunPod API key used to submit/poll jobs. |
| `RUNPOD_ENDPOINT_ID` | yes | The serverless endpoint running `worker/`. |
| `PUBLIC_BASE_URL` | recommended | Publicly reachable base URL of *this* server (no trailing slash). Falls back to the request origin. |
| `TRANSCODE_TOKEN` | optional | Shared bearer token for source/output endpoints. Auto-generated per process if unset (set it for stability across restarts). |
| `BODY_SIZE_LIMIT` | prod | adapter-node request body cap. The Dockerfile sets `Infinity` so large uploads work. |
| `RUNPOD_EXECUTION_TIMEOUT_MS`, `RUNPOD_TTL_MS` | optional | Job policy overrides. |

## Develop

```sh
bun install
bun run dev
```

Then set the env vars (e.g. in a `.env`) and open the dev server. Encoding requires a configured
RunPod endpoint and a reachable `PUBLIC_BASE_URL`.

## Build & run with Docker

```sh
docker build -t transcoding-demo .
docker run --rm -p 3000:3000 \
  -e RUNPOD_API_KEY=... \
  -e RUNPOD_ENDPOINT_ID=... \
  -e PUBLIC_BASE_URL=https://your-public-host \
  transcoding-demo
```

## Notes & limitations (it's a demo)

- All state is in RAM — restarting the server loses uploaded videos and outputs.
- Files are buffered fully in memory; there's a 2 GiB per-upload cap.
- Output GET endpoints are unauthenticated (random UUID paths); the source GET and all output PUTs
  require the bearer token.
- Encoding settings are stored per-browser in `localStorage`.
