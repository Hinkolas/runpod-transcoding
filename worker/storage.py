"""Pluggable storage backends.

Two transfer mechanisms, chosen per request:

- ``s3``   — boto3 download/upload. Works with AWS and any S3-compatible provider
             (Hetzner, Cloudflare R2, Backblaze, MinIO, ...). Credentials resolve
             per-request first, then fall back to worker env vars.
- ``http`` — download via a single GET (supports presigned URLs), upload via one
             PUT per output file to ``{baseUrl}/{relativePath}``. Needs no
             credentials; auth is whatever the caller puts in ``headers``.
"""

from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

import boto3
import requests
from botocore.config import Config

from schema import (
    Destination as DestinationConfig,
    HttpDestination,
    HttpSource,
    S3Destination,
    S3Source,
    Source as SourceConfig,
)

# (connect timeout, read timeout) for all HTTP transfers.
HTTP_TIMEOUT = (10, 600)
CHUNK_SIZE = 1024 * 1024  # 1 MiB streaming chunks


@dataclass
class StoredFile:
    relativePath: str  # posix path within the output tree, e.g. "720p/index.m3u8"
    location: str      # the s3 key OR the absolute http URL where the file landed


class Source(Protocol):
    def download(self, dest_path: Path) -> None: ...


class Destination(Protocol):
    def upload(self, directory: Path) -> list[StoredFile]: ...


# --- Shared helpers -------------------------------------------------------------


def content_type_for(path: Path) -> str | None:
    if path.suffix == ".m3u8":
        return "application/vnd.apple.mpegurl"
    if path.suffix == ".ts":
        return "video/mp2t"
    return mimetypes.guess_type(path.name)[0]


def iter_output_files(directory: Path) -> Iterator[tuple[Path, str]]:
    """Yield (absolute_path, posix_relative_path) for every file under ``directory``."""
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            yield path, path.relative_to(directory).as_posix()


def _resolve_s3_credentials(
    access_key_id: str | None, secret_access_key: str | None
) -> tuple[str, str]:
    access_key = (
        access_key_id
        or os.getenv("S3_ACCESS_KEY_ID")
        or os.getenv("AWS_ACCESS_KEY_ID")
    )
    secret_key = (
        secret_access_key
        or os.getenv("S3_SECRET_ACCESS_KEY")
        or os.getenv("AWS_SECRET_ACCESS_KEY")
    )
    if not access_key or not secret_key:
        raise RuntimeError(
            "S3 credentials are missing. Provide accessKeyId/secretAccessKey in the "
            "request's storage config, or set S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY "
            "(or AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY) in the worker env."
        )
    return access_key, secret_key


def _make_s3_client(
    endpoint_url: str | None,
    region: str,
    access_key_id: str | None,
    secret_access_key: str | None,
):
    access_key, secret_key = _resolve_s3_credentials(access_key_id, secret_access_key)
    kwargs = dict(
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )
    if endpoint_url:  # omit for real AWS so boto3 picks the regional endpoint
        kwargs["endpoint_url"] = endpoint_url
    return boto3.client("s3", **kwargs)


# --- S3 backend -----------------------------------------------------------------


class S3SourceBackend:
    def __init__(self, config: S3Source) -> None:
        self._config = config
        self._client = _make_s3_client(
            config.endpointUrl, config.region, config.accessKeyId, config.secretAccessKey
        )

    def download(self, dest_path: Path) -> None:
        self._client.download_file(self._config.bucket, self._config.key, str(dest_path))


class S3DestinationBackend:
    def __init__(self, config: S3Destination) -> None:
        self._config = config
        self._client = _make_s3_client(
            config.endpointUrl, config.region, config.accessKeyId, config.secretAccessKey
        )

    def upload(self, directory: Path) -> list[StoredFile]:
        prefix = self._config.prefix.rstrip("/")
        stored: list[StoredFile] = []
        for path, rel in iter_output_files(directory):
            key = f"{prefix}/{rel}"
            content_type = content_type_for(path)
            extra_args = {"ContentType": content_type} if content_type else {}
            self._client.upload_file(str(path), self._config.bucket, key, ExtraArgs=extra_args)
            stored.append(StoredFile(relativePath=rel, location=key))
        return stored


# --- HTTP backend ---------------------------------------------------------------


class HttpSourceBackend:
    def __init__(self, config: HttpSource) -> None:
        self._config = config

    def download(self, dest_path: Path) -> None:
        with requests.get(
            self._config.url, headers=self._config.headers, stream=True, timeout=HTTP_TIMEOUT
        ) as response:
            response.raise_for_status()
            with open(dest_path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        handle.write(chunk)


class HttpDestinationBackend:
    def __init__(self, config: HttpDestination) -> None:
        self._config = config

    def upload(self, directory: Path) -> list[StoredFile]:
        base = self._config.baseUrl.rstrip("/")
        session = requests.Session()
        stored: list[StoredFile] = []
        try:
            for path, rel in iter_output_files(directory):
                url = f"{base}/{rel}"
                headers = dict(self._config.headers)
                content_type = content_type_for(path)
                if content_type:
                    headers["Content-Type"] = content_type
                with open(path, "rb") as handle:
                    response = session.put(url, data=handle, headers=headers, timeout=HTTP_TIMEOUT)
                response.raise_for_status()
                stored.append(StoredFile(relativePath=rel, location=url))
        finally:
            session.close()
        return stored


# --- Factories ------------------------------------------------------------------


def make_source(config: SourceConfig) -> Source:
    if isinstance(config, S3Source):
        return S3SourceBackend(config)
    if isinstance(config, HttpSource):
        return HttpSourceBackend(config)
    raise ValueError(f"Unsupported source type: {getattr(config, 'type', config)!r}")


def make_destination(config: DestinationConfig) -> Destination:
    if isinstance(config, S3Destination):
        return S3DestinationBackend(config)
    if isinstance(config, HttpDestination):
        return HttpDestinationBackend(config)
    raise ValueError(f"Unsupported destination type: {getattr(config, 'type', config)!r}")
