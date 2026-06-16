# runpod-transcoding

A general-purpose, self-hostable [RunPod serverless](https://docs.runpod.io/serverless/overview) worker that
transcodes videos into adaptive **HLS** renditions (multiple quality levels) + a master playlist + a poster
frame. Every job is driven entirely by the request — the quality ladder (resolution, rate control, codec,
audio) and **where the source is read from / where the output is written to** (S3 or any HTTP endpoint).

| Path | What it is |
|------|------------|
| [`worker/`](worker) | The RunPod serverless worker (Python + ffmpeg). The thing you deploy. |
| [`example/`](example) | A SvelteKit demo UI that drives the worker over HTTP transport (upload → encode → play). |

## Deploy the worker

### Option A — use the prebuilt image (recommended)

Each version tag publishes a multi-tag image to the GitHub Container Registry:

```
ghcr.io/hinkolas/runpod-transcoding-worker:0.1.0   # exact version
ghcr.io/hinkolas/runpod-transcoding-worker:0.1     # latest patch of 0.1
ghcr.io/hinkolas/runpod-transcoding-worker:latest  # latest release
```

1. In the RunPod console → **Serverless → New Endpoint**, set the container image to
   `ghcr.io/hinkolas/runpod-transcoding-worker:latest` (or pin a version).
2. Pick a **CPU** worker (more vCPUs = faster parallel renditions).
3. Optionally set `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` env vars if you want a default "house" S3 account
   (per-request credentials and the `http` backend need nothing).
4. Submit jobs to `https://api.runpod.ai/v2/<ENDPOINT_ID>/run` — see [`worker/README.md`](worker/README.md)
   for the full job schema, storage backends, and response shape.

> **GHCR visibility:** the package is private by default. Either make it public
> (repo → Packages → the image → *Package settings* → *Change visibility*), or add registry pull credentials
> in your RunPod endpoint (username = your GitHub username, password = a PAT with `read:packages`).

### Option B — build it yourself

```sh
docker build -t runpod-transcoding-worker worker/
```

Then push it to a registry RunPod can pull and point an endpoint at it. See [`worker/README.md`](worker/README.md).

## Releasing (CI)

Pushing a semver tag builds and publishes the worker image automatically via
[`.github/workflows/worker-image.yml`](.github/workflows/worker-image.yml):

```sh
git tag v0.1.0
git push origin v0.1.0
```

This produces `ghcr.io/hinkolas/runpod-transcoding-worker` tagged `0.1.0`, `0.1`, and `latest`. The workflow
uses the built-in `GITHUB_TOKEN` (no secrets to configure) and builds `linux/amd64` (RunPod's worker arch).

## Demo UI

See [`example/README.md`](example/README.md) to run the SvelteKit demo (upload a video, tweak settings,
encode, and play the HLS result). It uses the worker's HTTP transport and keeps state in memory — no database.

## License

[MIT](LICENSE)
