# CPU-only ffmpeg worker (no CUDA). Targets RunPod serverless CPU endpoints.
#
# GPU/NVENC variant: swap this base image for an NVIDIA CUDA + ffmpeg image and
# change encoder_args() in transcode.py to use h264_nvenc / hevc_nvenc. The
# encoder is isolated to that one function, so no other code needs to change.
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
	&& apt-get install -y --no-install-recommends \
		ffmpeg \
		ca-certificates \
	&& rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir -r requirements.txt

COPY handler.py schema.py transcode.py storage.py ./

CMD ["python3", "-u", "handler.py"]
