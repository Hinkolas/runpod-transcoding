# Transcoding Worker

A general-purpose [RunPod serverless](https://docs.runpod.io/serverless/overview) worker that transcodes a
source video into [HLS](https://en.wikipedia.org/wiki/HTTP_Live_Streaming) renditions (multiple quality levels)
plus a master playlist and a poster frame.

Everything about a job is driven by the request — the quality renditions (resolution, bitrate, codec, audio),
and **where the source is fetched from and where the output is written to**. Storage is pluggable per request,
so you can point one job at S3 and another at an HTTP endpoint on your own server (e.g. an app that stores
files on its local filesystem). No per-bucket configuration is baked into the worker.

- **CPU-only** encoding (libx264 / libx265). Runs on cheap RunPod CPU endpoints.
- Each rendition encodes in its own ffmpeg process, in parallel — auto-tuned to the box's core count.
- Output: `master.m3u8` + `<label>/index.m3u8` + `<label>/segment_00000.ts …` per rendition + `poster.jpg`.

---

## Job input

The RunPod job body is `{ "input": <TranscodeInput> }`.

### `TranscodeInput`

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `appVideoId` | string | — | Your correlation id. Echoed back in the response. |
| `source` | [`Source`](#source-download) | — | Where to download the source video from. |
| `destination` | [`Destination`](#destination-upload) | — | Where to upload the HLS output to. |
| `renditions` | [`Rendition`](#rendition)[] | — | At least one. Quality levels to produce. |
| `allowUpscale` | bool | `false` | If false, renditions taller than the source are dropped (smallest kept as a floor). |
| `segmentSeconds` | int (2–20) | `6` | HLS segment length. Keyframes are aligned to this. |
| `threads` | int | `0` | ffmpeg threads per encode. `0` = auto-tune to CPU count. |
| `metadata` | object | `{}` | Free-form passthrough, echoed verbatim in the response. Tag jobs for usage attribution, e.g. `{ "app": "app-a" }` (see [Usage tracking](#usage-tracking)). |

### `Rendition`

Set **exactly one** of `crf` or `videoBitrate` for rate control (see [Rate control](#rate-control)); `maxrate`/`bufsize` are always applied as a peak cap. Everything else has a sensible default.

| Field | Type | Default | ffmpeg |
|-------|------|---------|--------|
| `label` | string | — | Output subdirectory name, e.g. `"720p"`. |
| `height` | int | — | Target height; width is derived from aspect ratio (`scale=-2:height`). |
| `width` | int | `null` | Optional explicit width (otherwise computed). |
| `crf` | int 0–51 | `null` | `-crf` constant-quality target. Set this **or** `videoBitrate`. |
| `videoBitrate` | string | `null` | `-b:v` average bitrate (ABR). Set this **or** `crf`. |
| `maxrate` | string | — | `-maxrate` peak cap, e.g. `"5000k"`. |
| `bufsize` | string | — | `-bufsize`, e.g. `"10000k"`. |
| `codec` | `"h264"` \| `"h265"` | `"h264"` | h264 is the universal browser/native default. |
| `audioBitrate` | string | `"128k"` | `-b:a`. |
| `preset` | string | `"veryfast"` | `-preset` — slower = smaller files at the same quality; use `medium`/`slow` for VOD. |
| `pixelFormat` | string | `"yuv420p"` | `-pix_fmt`. |
| `audioCodec` | string | `"aac"` | `-c:a`. |
| `audioSampleRate` | int | `48000` | `-ar`. |
| `audioChannels` | int | `2` | `-ac`. |

#### Rate control

- **CRF (recommended for VOD)** — set `crf` (e.g. `21`), omit `videoBitrate`. The encoder spends the bits each scene needs for constant quality; `maxrate`/`bufsize` cap the peak so a complex scene can't exceed a viewer's bandwidth. Best quality-per-byte.
- **ABR** — set `videoBitrate` (e.g. `"2800k"`), omit `crf`, for a fixed average-bitrate ladder.
- Pair either with a slower `preset` (`medium`/`slow`): you encode once and serve many times, so the extra encode time buys smaller files forever.

### `Source` (download)

Discriminated on `type`.

**`s3`** — boto3 download (AWS or any S3-compatible provider):

| Field | Type | Default |
|-------|------|---------|
| `type` | `"s3"` | — |
| `bucket` | string | — |
| `key` | string | — |
| `endpointUrl` | string | `null` (real AWS regional endpoint) |
| `region` | string | `"auto"` |
| `accessKeyId` | string | `null` → env fallback |
| `secretAccessKey` | string | `null` → env fallback |

**`http`** — a single GET (the `url` may be a **presigned** S3 GET URL):

| Field | Type | Default |
|-------|------|---------|
| `type` | `"http"` | — |
| `url` | string | — |
| `headers` | object | `{}` |

### `Destination` (upload)

**`s3`** — boto3 upload under a prefix, with correct `Content-Type` per file:

| Field | Type | Default |
|-------|------|---------|
| `type` | `"s3"` | — |
| `bucket` | string | — |
| `prefix` | string | — |
| `endpointUrl` | string | `null` |
| `region` | string | `"auto"` |
| `accessKeyId` | string | `null` → env fallback |
| `secretAccessKey` | string | `null` → env fallback |

**`http`** — one HTTP `PUT` per output file to `{baseUrl}/{relativePath}` (e.g. `…/720p/segment_00001.ts`),
with the file's `Content-Type` set. Your server maps the relative path onto wherever it stores files (e.g. local
filesystem). Auth is whatever you put in `headers`:

| Field | Type | Default |
|-------|------|---------|
| `type` | `"http"` | — |
| `baseUrl` | string | — |
| `headers` | object | `{}` (e.g. `{"Authorization": "Bearer …"}`) |

---

## Example requests

**S3 in, S3 out** (credentials omitted → uses the worker's env "house account"):

```json
{
  "input": {
    "appVideoId": "video-123",
    "source": {
      "type": "s3",
      "bucket": "my-bucket",
      "key": "uploads/video-123/source.mp4",
      "endpointUrl": "https://fsn1.your-objectstorage.com",
      "region": "fsn1"
    },
    "destination": {
      "type": "s3",
      "bucket": "my-bucket",
      "prefix": "outputs/video-123",
      "endpointUrl": "https://fsn1.your-objectstorage.com",
      "region": "fsn1"
    },
    "renditions": [
      { "label": "480p",  "height": 480,  "crf": 22, "maxrate": "1400k", "bufsize": "2800k",  "preset": "medium" },
      { "label": "720p",  "height": 720,  "crf": 21, "maxrate": "2800k", "bufsize": "5600k",  "preset": "medium" },
      { "label": "1080p", "height": 1080, "crf": 21, "maxrate": "5000k", "bufsize": "10000k", "preset": "medium", "audioBitrate": "192k" }
    ]
  }
}
```

**Presigned HTTP in, HTTP out** (no credentials anywhere — ideal for an app using its local filesystem):

```json
{
  "input": {
    "appVideoId": "video-123",
    "source": {
      "type": "http",
      "url": "https://my-app.example.com/files/source.mp4?signature=…"
    },
    "destination": {
      "type": "http",
      "baseUrl": "https://my-app.example.com/api/transcode-output/video-123",
      "headers": { "Authorization": "Bearer SHARED_SECRET" }
    },
    "renditions": [
      { "label": "720p", "height": 720, "crf": 21, "maxrate": "2800k", "bufsize": "5600k", "preset": "medium" }
    ]
  }
}
```

For the HTTP destination, your endpoint receives a `PUT` for each file. Reconstruct the tree from the path,
e.g. `PUT /api/transcode-output/video-123/720p/index.m3u8`, then `…/720p/segment_00000.ts`, and finally
`…/master.m3u8` and `…/poster.jpg`. Stream the request body to disk and return `2xx`.

---

## Response

```jsonc
{
  "appVideoId": "video-123",
  "metadata": { "app": "app-a" },          // echoed verbatim from the request
  "destinationType": "s3",                 // or "http"
  "outputs": [                              // every uploaded file
    { "relativePath": "master.m3u8", "location": "outputs/video-123/master.m3u8" },
    { "relativePath": "720p/index.m3u8", "location": "outputs/video-123/720p/index.m3u8" }
    // …segments, poster…
  ],
  "renditions": [
    { "label": "720p", "height": 720, "width": 1280, "videoBitrate": "2800k", "audioBitrate": "128k",
      "bandwidth": 2928000, "playlistFile": "720p/index.m3u8", "segmentPrefix": "720p",
      "location": "outputs/video-123/720p/index.m3u8" }
  ],
  "probe": { "width": 1920, "height": 1080, "durationSeconds": 60.0, "codec": "h264", "bitrate": 5000000 },
  "uploadedObjectCount": 23,
  "encoder": "libx264/libx265 (cpu)",
  "parallelism": { "workers": 3, "threadsPerEncode": 10 },
  "durationMs": 45000,

  // s3 destination:
  "masterPlaylistKey": "outputs/video-123/master.m3u8",
  "posterKey": "outputs/video-123/poster.jpg"
  // http destination instead carries:
  // "masterPlaylistUrl": "https://…/master.m3u8",
  // "posterUrl": "https://…/poster.jpg"
}
```

`location` is the S3 key for an `s3` destination, or the absolute URL for an `http` destination. `posterKey` /
`posterUrl` is `null` if poster extraction failed (it is best-effort and never fails the job).

---

## Usage tracking

To attribute usage per calling app/customer:

- **Tag each job** via `metadata`, e.g. `"metadata": { "app": "app-a" }`. The worker echoes it back verbatim, so each result is self-describing.
- **Billed seconds come from RunPod, not the worker.** RunPod's job status/webhook payload includes `executionTime` (ms of compute you're billed for) and `delayTime` (ms queued, not billed). The worker's own `durationMs` is a close secondary measure (download + encode + upload) but excludes cold start and RunPod's rounding — use `executionTime` for billing.
- **The RunPod dashboard aggregates per endpoint**, so it can't split usage by your `app` within one shared endpoint. Capture `executionTime` + `metadata.app` per job in your own app/DB and sum per app over the period. (One endpoint per app is the only dashboard-native split.)

The worker stays billing-agnostic — it just echoes your tags and reports its own timing; cost/rate logic lives in your app.

---

## Credentials & security

The worker holds **no per-bucket configuration**. Credentials for the `s3` backend resolve in this order:

1. `accessKeyId` / `secretAccessKey` in the request's storage config (per-request, any bucket), then
2. `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` env vars, then
3. `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` env vars.

Guidance:

- **Single bucket for everything?** Set the env "house account" once on the endpoint and omit credentials from
  every request.
- **Downloads:** prefer the `http` source with a **presigned GET URL** — no download credentials ever transit
  the job payload.
- **Per-request credentials end up in the RunPod job payload (and likely its logs).** Only put them there for
  genuinely multi-tenant destinations, and prefer scoped / short-lived keys when you do.
- **Local-filesystem apps:** use the `http` destination with an `Authorization` header (or your own signed URL
  scheme) — no S3 at all.

### Env vars

| Var | Purpose |
|-----|---------|
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | Optional "house account" for the `s3` backend. |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Recognised as a fallback. |

The `http` backend needs no env vars.

---

## Deploy to RunPod

1. **Build & push the image** to a registry RunPod can pull:
   ```bash
   docker build -t YOUR_REGISTRY/transcoding-worker:latest worker/
   docker push YOUR_REGISTRY/transcoding-worker:latest
   ```
2. **Create a Serverless endpoint** (RunPod console → Serverless → New Endpoint):
   - Container image: `YOUR_REGISTRY/transcoding-worker:latest`
   - Pick a **CPU** worker. More vCPUs = faster parallel renditions (see `plan_concurrency`).
   - Set env vars only if you want an S3 house account (see above).
3. **Submit jobs** to `https://api.runpod.ai/v2/<ENDPOINT_ID>/run` (async, with a webhook) or `/runsync`,
   with the `{ "input": … }` body shown above.

### Local container check (optional)

You can validate the built container against your own payload before deploying — write your `{ "input": … }`
to a JSON file and run:

```bash
docker run --rm -v "$PWD/my_job.json:/app/my_job.json" \
  -e S3_ACCESS_KEY_ID=… -e S3_SECRET_ACCESS_KEY=… \
  YOUR_REGISTRY/transcoding-worker:latest \
  python3 -u handler.py --test_input my_job.json
```

This runs the full handler once and prints the response JSON.

---

## Module layout

| File | Responsibility |
|------|----------------|
| `schema.py` | Pydantic request contract (the models documented above). No I/O. |
| `transcode.py` | ffmpeg / ffprobe logic on local files. |
| `storage.py` | `s3` and `http` source/destination backends + credential resolution. |
| `handler.py` | Orchestration + RunPod entrypoint. |
