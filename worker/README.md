# Transcoding Worker

A general-purpose [RunPod serverless](https://docs.runpod.io/serverless/overview) worker that transcodes a
source video or audio file into [HLS](https://en.wikipedia.org/wiki/HTTP_Live_Streaming) renditions (multiple
quality levels) plus a master playlist and a poster, and optionally into audio-only renditions (podcasts, music),
a progressive mp3/m4a download and waveform peaks. This document describes release **v0.2.1**.

Everything about a job is driven by the request — the quality renditions (resolution, bitrate, codec, audio),
and **where the source is fetched from and where the output is written to**. Storage is pluggable per request,
so you can point one job at S3 and another at an HTTP endpoint on your own server (e.g. an app that stores
files on its local filesystem). No per-bucket configuration is baked into the worker.

- **CPU-only** encoding (libx264 / libx265). Runs on cheap RunPod CPU endpoints.
- Each rendition encodes in its own ffmpeg process, in parallel — auto-tuned to the box's core count.
- Video sources, audio-only sources (mp3, m4a, wav, flac, an mp4 without video) and audio files with cover art.
- There is no mode switch: the job lists the outputs it wants, the worker produces what the source supports
  (video outputs need a video stream, audio outputs an audio stream) and reports the rest under
  [`skipped`](#skipped-outputs). See [Output layout](#output-layout).
- HTTP transfers retry transient failures (see [Retries](#retries)).

---

## Job input

The RunPod job body is `{ "input": <TranscodeInput> }`.

### `TranscodeInput`

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `appVideoId` | string | — | Your correlation id. Echoed back in the response. |
| `source` | [`Source`](#source-download) | — | Where to download the source file from. |
| `destination` | [`Destination`](#destination-upload) | — | Where to upload the output to. |
| `renditions` | [`Rendition`](#rendition)[] | `[]` | Video quality levels. Produced only if the source has a video stream. |
| `audioRenditions` | [`AudioRendition`](#audiorendition)[] | `[]` | Audio-only HLS renditions. Produced only if the source has an audio stream. |
| `audioDownload` | [`AudioDownload`](#audiodownload) | `null` | One progressive `audio.mp3` / `audio.m4a`. Needs an audio stream. |
| `waveform` | [`Waveform`](#waveform) | `null` | Waveform peaks (`waveform.json`). Needs an audio stream. |
| `loudness` | [`Loudness`](#loudness) | `null` | Loudness normalisation for `audioRenditions` and `audioDownload`. |
| `thumbnails` | [`Thumbnails`](#thumbnails) | `null` | Opt-in scrub thumbnails (storyboard sprites + WebVTT). Needs a video stream. Omit ⇒ none generated. |
| `allowUpscale` | bool | `false` | If false, renditions taller than the source are dropped (smallest kept as a floor). |
| `segmentSeconds` | int (2–20) | `6` | HLS segment length. Keyframes are aligned to this. |
| `threads` | int | `0` | ffmpeg threads per encode. `0` = auto-tune to CPU count. |
| `metadata` | object | `{}` | Free-form passthrough, echoed verbatim in the response. Tag jobs for usage attribution, e.g. `{ "app": "app-a" }` (see [Usage tracking](#usage-tracking)). |

A job must request at least one of `renditions`, `audioRenditions`, `audioDownload` or `waveform` (a waveform
alone is fine, e.g. to regenerate just that output). Jobs written for v0.1 (only `renditions`) behave as before.

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

The audio settings apply only when the source has audio; a video without audio gives video-only renditions.
The audio muxed into video renditions is never loudness-normalised.

#### Rate control

- **CRF (recommended for VOD)** — set `crf` (e.g. `21`), omit `videoBitrate`. The encoder spends the bits each scene needs for constant quality; `maxrate`/`bufsize` cap the peak so a complex scene can't exceed a viewer's bandwidth. Best quality-per-byte.
- **ABR** — set `videoBitrate` (e.g. `"2800k"`), omit `crf`, for a fixed average-bitrate ladder.
- Pair either with a slower `preset` (`medium`/`slow`): you encode once and serve many times, so the extra encode time buys smaller files forever.

### `AudioRendition`

One audio-only HLS rendition: AAC-LC (`-c:a aac`) in MPEG-TS segments of `segmentSeconds`, written to
`audio/<label>/index.m3u8` and listed in `audio.m3u8`.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `label` | string | — | `^[A-Za-z0-9_-]{1,32}$`, unique within `audioRenditions`. |
| `bitrate` | string | `"128k"` | `-b:a`. |
| `channels` | int 1–8 | `2` | `-ac`. |
| `sampleRate` | 22050 \| 24000 \| 32000 \| 44100 \| 48000 | `48000` | `-ar`. |

### `AudioDownload`

One progressive file. mp3 is CBR libmp3lame with an ID3v2.3 tag (`-id3v2_version 3`); m4a is AAC with
`-movflags +faststart`. Cover art is never embedded.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `format` | `"mp3"` \| `"m4a"` | — | Output is `audio.mp3` or `audio.m4a`. |
| `bitrate` | string | `"128k"` | `-b:a`. |
| `channels` | int | `2` | 1–2 for mp3, 1–8 for m4a. |
| `sampleRate` | 22050 \| 24000 \| 32000 \| 44100 \| 48000 | `null` | `null` ⇒ 44100 for mp3, 48000 for m4a. |

### `Waveform`

Peaks in the [audiowaveform](https://github.com/bbc/audiowaveform) / [peaks.js](https://github.com/bbc/peaks.js)
JSON format, version 2, written to `waveform.json`:

```json
{"version":2,"channels":1,"sample_rate":16000,"samples_per_pixel":800,"bits":8,"length":72000,"data":[-12,13,-40,38]}
```

The audio stream is decoded once to mono 16-bit PCM at 16 kHz and streamed through a min/max fold, one
`[min, max]` pair per `samples_per_pixel = 16000 / peaksPerSecond` samples (rounded down), so memory stays flat for
long files (a 1-hour mp3 takes about 2 s). 8-bit values are the 16-bit sample `>> 8`; nothing is normalised. The
waveform is computed from the original audio, never the loudness-normalised one.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `peaksPerSecond` | int 1–100 | `20` | Peak pairs per second of audio. |
| `bits` | 8 \| 16 | `8` | Value range of `data`: −128..127 or −32768..32767. |

Generation is **best-effort**: on failure there is no `waveform.json`, the response's `waveform` is `null` and
the job still succeeds.

### `Loudness`

Two-pass EBU R128 normalisation with ffmpeg's `loudnorm`, applied to `audioRenditions` and `audioDownload` only.
Pass 1 measures the audio stream once (`print_format=json`); pass 2 runs in every audio encode with the measured
values and `linear=true`. If the measurement fails (including silent audio), the job fails.

Loudnorm is the most expensive audio step: it works at 192 kHz internally, so the measurement and every
normalised encode each cost roughly a minute of CPU per hour of audio. A 1-hour podcast job (two renditions,
mp3, waveform) took about 40 s without loudness and about 3 minutes with it on a fast desktop CPU.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `targetLufs` | number −70..−5 | `-16` | Integrated loudness target (`I`). |
| `truePeak` | number −9..0 | `-1.5` | Maximum true peak in dBTP (`TP`). |
| `lra` | number 1..50 | `11` | Loudness range target (`LRA`). |

### `Thumbnails`

Opt-in **scrub thumbnails** — the preview frames a player shows when hovering the seek bar. When present, the worker samples frames across the source, packs them into **sprite-sheet** mosaics, and writes a **WebVTT** index (`storyboard.vtt`) that maps each timestamp to a crop region of a sprite (`storyboard_000.jpg#xywh=x,y,w,h`). Every thumbnail-aware web player (Vidstack, Plyr, video.js, …) consumes this format. Omit the whole object ⇒ no thumbnails are generated.

Set **exactly one** of `intervalSeconds` / `targetCount` for density.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `intervalSeconds` | float | `null` | One thumbnail every N seconds. Set this **or** `targetCount`. |
| `targetCount` | int | `null` | Aim for ~this many thumbnails total (the interval is derived from duration). Set this **or** `intervalSeconds`. |
| `width` | int | `240` | Per-thumbnail width in px; height is derived from the source aspect ratio (rounded to even). |
| `columns` | int | `5` | Thumbnails per sprite row. |
| `rows` | int | `5` | Thumbnails per sprite column. `columns × rows` = thumbnails per sprite sheet. |
| `format` | `"jpeg"` \| `"webp"` | `"jpeg"` | Sprite image format. webp is ~25–30% smaller at equal quality (needs an ffmpeg built with libwebp — the provided image is). |
| `quality` | int 1–100 | `75` | Normalized quality, higher = better (mapped to mjpeg `-q:v` for jpeg, libwebp `-quality` for webp). |

Generation is **best-effort**: if it fails (or the source reports no duration), the job still succeeds and the storyboard fields come back `null`. It runs concurrently with the rendition encodes, so it adds little wall-clock on a multi-core box. Note that native `<video controls>` and hls.js don't render thumbnails — point a thumbnail-aware player at `storyboard.vtt` to display them.

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
e.g. `PUT /api/transcode-output/video-123/720p/segment_00000.ts`, then `…/720p/index.m3u8`, then `…/master.m3u8`,
`…/poster.jpg`, and — if `thumbnails` was requested — `…/storyboard_000.jpg` + `…/storyboard.vtt`
(see [Upload order](#upload-order)). Stream the request body to disk and return `2xx`.

**Audio only (podcast):** two AAC renditions, an mp3 download, a waveform and loudness normalisation to −16 LUFS.
For an mp3 with cover art, the cover becomes `poster.jpg`.

```json
{
  "input": {
    "appVideoId": "episode-42",
    "source": { "type": "http", "url": "https://my-app.example.com/files/episode-42.mp3?signature=…" },
    "destination": {
      "type": "http",
      "baseUrl": "https://my-app.example.com/api/transcode-output/episode-42",
      "headers": { "Authorization": "Bearer SHARED_SECRET" }
    },
    "audioRenditions": [
      { "label": "64k", "bitrate": "64k", "channels": 1 },
      { "label": "128k", "bitrate": "128k" }
    ],
    "audioDownload": { "format": "mp3", "bitrate": "128k" },
    "waveform": { "peaksPerSecond": 20, "bits": 8 },
    "loudness": { "targetLufs": -16, "truePeak": -1.5 }
  }
}
```

**Video plus podcast:** the usual video ladder, plus an audio-only stream and download of the same recording
(e.g. a video podcast). The video renditions keep the original audio; only the audio-only outputs are normalised.

```json
{
  "input": {
    "appVideoId": "episode-43",
    "source": { "type": "http", "url": "https://my-app.example.com/files/episode-43.mp4?signature=…" },
    "destination": {
      "type": "http",
      "baseUrl": "https://my-app.example.com/api/transcode-output/episode-43",
      "headers": { "Authorization": "Bearer SHARED_SECRET" }
    },
    "renditions": [
      { "label": "480p",  "height": 480,  "crf": 22, "maxrate": "1400k", "bufsize": "2800k" },
      { "label": "1080p", "height": 1080, "crf": 21, "maxrate": "5000k", "bufsize": "10000k" }
    ],
    "thumbnails": { "intervalSeconds": 5 },
    "audioRenditions": [{ "label": "128k" }],
    "audioDownload": { "format": "m4a" },
    "waveform": {},
    "loudness": {}
  }
}
```

If the same job is sent for an audio-only file, the video renditions and thumbnails are listed under `skipped`
with reason `no_video_stream` and the audio outputs are produced as usual.

---

## Output layout

All paths are relative to the destination (`prefix` for s3, `baseUrl` for http):

```
master.m3u8                           video master; only if video renditions were made
<label>/index.m3u8                    video rendition (with the source audio, if any)
<label>/segment_00000.ts …
audio.m3u8                            audio-only master; only if audio renditions were made
audio/<label>/index.m3u8              audio-only rendition (AAC-LC)
audio/<label>/segment_00000.ts …
audio.mp3 | audio.m4a                 progressive download
waveform.json                         waveform peaks
poster.jpg                            poster (video frame or cover art)
storyboard.vtt, storyboard_000.<jpg|webp> …   scrub thumbnails
```

Both masters are HLS version 3 with variants sorted by `BANDWIDTH`:

```
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=1528000,RESOLUTION=854x480,CODECS="avc1.640029,mp4a.40.2"
480p/index.m3u8
```

```
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=141200,CODECS="mp4a.40.2"
audio/128k/index.m3u8
```

- **Video `CODECS`** is read from the first encoded segment of each rendition (ffprobe profile and level), so it
  describes what was actually encoded: H.264 is `avc1.PPCCLL` in lowercase hex (High `64 00`, Main `4d 40`,
  Constrained Baseline `42 e0`, Baseline `42 00`; level 4.1 ⇒ `29`), HEVC is `hvc1.1.6.L<level>.B0` (Main) or
  `hvc1.2.4.L<level>.B0` (Main 10). The audio part (`mp4a.40.2` for AAC-LC) is added when the rendition has
  audio. If any part is unknown, the variant has no `CODECS` and the rendition's `codecs` is `""`.
- **Video `BANDWIDTH`** is `videoBitrate`, else `maxrate`, else the measured segment rate, plus `audioBitrate`
  when the rendition has audio. `RESOLUTION` is present when the width is known.
- **Audio `BANDWIDTH`** is the larger of the nominal bitrate and the measured rate (segment bytes × 8 / duration),
  which includes the MPEG-TS overhead. Audio `CODECS` is always `mp4a.40.2`.

### Poster

- Video source: a frame 3 s in (or half-way for shorter files) at the source's resolution, scaled down to fit
  1920×1920 (never upscaled; before v0.2.1 it was always 640 px wide). `poster.source = "frame"`.
- No video but embedded cover art: the cover art, re-encoded to JPEG and scaled down to fit 3000×3000 (never
  upscaled; 1280×1280 before v0.2.1). `poster.source = "coverArt"`.
- Neither: no poster. The poster is best-effort and never fails the job.

A video stream flagged as an attached picture is always treated as cover art, never as video. Every ffmpeg
command maps the chosen streams by index: the video stream is the first non-cover-art video stream (preferring
the default one), the audio stream is the default audio stream or else the first.

### Skipped outputs

Requested outputs the source can't support are listed in the response's `skipped`, never as an error:

| `output` | `label` | `reason` | When |
|----------|---------|----------|------|
| `renditions` | rendition label | `no_video_stream` | One entry per requested video rendition, if the source has no video. |
| `thumbnails` | `null` | `no_video_stream` | Thumbnails requested, source has no video. |
| `audioRenditions` | rendition label | `no_audio_stream` | One entry per requested audio rendition, if the source has no audio. |
| `audioDownload` | `null` | `no_audio_stream` | |
| `waveform` | `null` | `no_audio_stream` | |

If that leaves nothing to produce, the job still succeeds: the response has empty `renditions`, `audio: null`,
`waveform: null`, the full `probe` and `skipped`, so the caller can learn what the source contains. Only a source
with neither audio nor video fails (`Source has no audio or video stream`). Renditions dropped because they are
taller than the source (`allowUpscale: false`) are not listed under `skipped`, as before.

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
    { "label": "720p", "height": 720, "width": 1280, "crf": null, "videoBitrate": "2800k", "audioBitrate": "128k",
      "bandwidth": 2928000, "codecs": "avc1.640029,mp4a.40.2",
      "playlistFile": "720p/index.m3u8", "segmentPrefix": "720p",
      "location": "outputs/video-123/720p/index.m3u8" }
  ],
  "storyboard": {                          // null unless `thumbnails` was requested (or if it failed)
    "interval": 5.0, "thumbWidth": 240, "thumbHeight": 136,
    "columns": 5, "rows": 5, "format": "jpeg",
    "thumbnailCount": 12, "spriteCount": 1, "vttFile": "storyboard.vtt",
    "location": "outputs/video-123/storyboard.vtt"
  },
  "probe": {
    "hasVideo": true, "hasAudio": true, "width": 1920, "height": 1080, "durationSeconds": 60.0,
    "videoCodec": "h264", "audioCodec": "aac", "audioChannels": 2, "audioSampleRate": 48000,
    "audioBitrate": 128000, "hasCoverArt": false, "format": "mov,mp4,m4a,3gp,3g2,mj2", "bitrate": 5000000,
    "videoStreamIndex": 0, "audioStreamIndex": 1,
    "codec": "h264"                        // alias of videoCodec, kept from v0.1
  },
  "audio": {                               // null unless an audio rendition or download was produced
    "masterPlaylist": "audio.m3u8",        // null if only a download was produced
    "renditions": [
      { "label": "128k", "bitrate": "128k", "channels": 2, "sampleRate": 48000,
        "bandwidth": 141200, "codecs": "mp4a.40.2", "playlistFile": "audio/128k/index.m3u8" }
    ],
    "download": { "file": "audio.mp3", "format": "mp3", "bitrate": "128k", "channels": 2,
                  "sampleRate": 44100, "sizeBytes": 960417, "durationSeconds": 60.03 }   // or null
  },
  "waveform": { "file": "waveform.json", "peaksPerSecond": 20, "bits": 8, "length": 1200 },  // or null
  "poster": { "file": "poster.jpg", "source": "frame" },     // "frame" | "coverArt", or null
  "loudness": { "measuredI": -23.4, "measuredTP": -4.1, "measuredLRA": 6.2,               // null unless applied
                "targetLufs": -16, "truePeak": -1.5 },
  "skipped": [],                           // see "Skipped outputs"
  "uploadedObjectCount": 23,
  "encoder": "libx264/libx265 (cpu)",
  "parallelism": { "workers": 3, "threadsPerEncode": 10 },
  "durationMs": 45000,

  // s3 destination:
  "masterPlaylistKey": "outputs/video-123/master.m3u8",
  "posterKey": "outputs/video-123/poster.jpg",
  "storyboardVttKey": "outputs/video-123/storyboard.vtt"
  // http destination instead carries:
  // "masterPlaylistUrl": "https://…/master.m3u8",
  // "posterUrl": "https://…/poster.jpg",
  // "storyboardVttUrl": "https://…/storyboard.vtt"
}
```

`location` is the S3 key for an `s3` destination, or the absolute URL for an `http` destination.
`masterPlaylistKey` / `masterPlaylistUrl` is `null` when no video renditions were made. `posterKey` /
`posterUrl` is `null` if poster extraction failed (it is best-effort and never fails the job). `storyboardVttKey` /
`storyboardVttUrl` (and the `storyboard` block) are `null` unless `thumbnails` was requested, and `null` if
generation failed (also best-effort).

---

## Progress updates

While the job runs, the worker reports progress via `runpod.serverless.progress_update`. Poll
`GET https://api.runpod.ai/v2/{endpointId}/status/{jobId}`: while `status` is `IN_PROGRESS`, the `output` field
carries the latest update as a JSON string. On `COMPLETED` it is replaced by the [Response](#response) object above.

```jsonc
{ "status": "IN_PROGRESS", "output": "{\"phase\":\"encoding\",\"percent\":42}" }
```

`phase` is one of `downloading`, `probing`, `encoding`, `uploading`. `percent` (0–100) is present only during
`encoding` — it is the weighted mean completion across the parallel tasks (each video rendition weighs 1; each
audio rendition, the download, the waveform and the loudness measurement weigh 0.25), derived from each ffmpeg's
`out_time` against the probed source duration. Updates are throttled to ~1/s. **Progress is purely additive and best-effort:**
a fire-and-forget caller that only reads the terminal result is unaffected, and if no client polls mid-job nothing
about the final output changes.

---

## Retries

The `http` backends retry each transfer up to **5 attempts** with exponential backoff (1 s, 2 s, 4 s, 8 s) on
connection errors, timeouts and `408`, `425`, `429` and `5xx` responses. Other `4xx` responses fail at once.
Each `PUT` re-opens its file; a download that fails or is cut short restarts from the beginning. (The `s3`
backend uses boto3's own retries.)

## Upload order

Both destinations upload media segments first, then rendition playlists, then the masters (`audio.m3u8`,
`master.m3u8`), then the download, `waveform.json`, `poster.jpg` and finally the storyboard sprites and
`storyboard.vtt`. A playlist is therefore never visible before the files it references.

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
| `transcode.py` | ffmpeg / ffprobe logic on local files (probe, video and audio encodes, loudness, poster, storyboard, playlists). |
| `waveform.py` | Streaming waveform peaks (numpy). |
| `storage.py` | `s3` and `http` source/destination backends, retries, upload order, credential resolution. |
| `handler.py` | Orchestration + RunPod entrypoint. |
| `tests/` | pytest suite (see [Tests](#tests)). |

## Tests

```bash
cd worker
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest            # unit + integration (needs ffmpeg/ffprobe on PATH), ~15 s
.venv/bin/python -m pytest -m slow    # 1-hour waveform benchmark
.venv/bin/python tests/bench_waveform.py --minutes 60   # prints time and peak memory
```

The integration tests generate media with ffmpeg (`lavfi` sine/testsrc: mp3 with cover art, wav, m4a, audio-only
mp4, video with and without audio) and run `handle_transcode` against a local HTTP server that serves the source
and accepts the PUTs.

---

## Roadmap / TODO

### Test single-decode ladder generation (decode once, encode many)

**Current behaviour:** the ladder is produced by **N independent ffmpeg processes** — one per rendition — run in parallel via a `ThreadPoolExecutor` (`handler.py`), each with its own `-i source` (`transcode.transcode_rendition`). This saturates a many-core box well, but every rung **re-decodes the full source**, so an N-rung ladder decodes the source N times (plus another full decode pass for the storyboard).

**Idea to evaluate:** collapse the encodes into a **single ffmpeg command** that decodes once and fans the decoded frames out with a `split` filter, one scaled+encoded HLS output per rung:

```
ffmpeg -y -nostats -progress pipe:1 -i source \
  -filter_complex "[0:v]split=3[s0][s1][s2];\
                   [s0]scale=-2:1080[v0];[s1]scale=-2:720[v1];[s2]scale=-2:480[v2]" \
  -map "[v0]" -map 0:a? <rung-0 opts> -f hls ... 1080p/index.m3u8 \
  -map "[v1]" -map 0:a? <rung-1 opts> -f hls ... 720p/index.m3u8 \
  -map "[v2]" -map 0:a? <rung-2 opts> -f hls ... 480p/index.m3u8
```

Each `<rung-N opts>` block is exactly the existing `encoder_args(r)` + `rate_control_args(r)` + GOP/`force_key_frames`/audio/HLS flags — they compose per-output unchanged. The refactor is mostly building the `split` graph and repeating the `-map … playlist` block per rung.

**Why it could be worth it:**

- **Decodes the source once** instead of N times (also cuts source disk reads N→1).
- **Net-simplifies the Python:** one process means one aggregate `-progress` stream, so `EncodeProgress` (the per-rendition progress folding) and most of the `ThreadPoolExecutor` orchestration go away. Existing `run_with_progress` already consumes a single command's progress.
- Error handling is unchanged in practice — today a single rung failing already fails the whole job (`future.result()` re-raises); one process has the same single-exit-code semantics.

**The trade-off to measure:** a single ffmpeg process doesn't always saturate a high-core box as perfectly as N independent processes — the shared decode→filter stage is serial-ish and the slowest encoder can cause head-of-line blocking on frame delivery. So this trades *perfect multicore saturation* for *decode-the-source-once*. Net win depends on the source:

- Cheap-to-decode source + slow preset → encode dominates, decode saving is small; could even lose a little to reduced parallelism.
- Expensive-to-decode source (4K HEVC/AV1) + faster preset → decode is a real chunk, decode-once wins clearly.

**To do:** benchmark single-command vs the current N-process approach (wall-clock + RunPod billed seconds) on representative sources on an actual RunPod CPU box before committing. Keep `-threads:v:N ≈ cpu / len(renditions)` per output so each encoder still gets a thread budget (mirrors `plan_concurrency`). Trade-off note: lost per-rendition progress granularity (acceptable — the UI only needs an aggregate percent). Leave the storyboard as its own separate decode pass for now so a thumbnail failure can't kill the encode.
