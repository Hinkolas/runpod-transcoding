// S3 bucket for the uploaded source videos and the HLS output the worker PUTs
// back. Objects are keyed per video: `videos/<id>/source` and
// `videos/<id>/output/<path>`, so deleting a video is one prefix sweep.

import { env } from '$env/dynamic/private';
import {
	DeleteObjectCommand,
	GetObjectCommand,
	HeadBucketCommand,
	ListObjectsV2Command,
	NoSuchKey,
	PutObjectCommand,
	S3Client
} from '@aws-sdk/client-s3';
import { Upload } from '@aws-sdk/lib-storage';
import { Readable } from 'node:stream';

const globalRef = globalThis as unknown as { __transcodeS3?: S3Client };

function requireEnv(name: string): string {
	const value = env[name];
	if (!value) throw new Error(`${name} is not set`);
	return value;
}

function client(): S3Client {
	return (globalRef.__transcodeS3 ??= new S3Client({
		endpoint: requireEnv('S3_ENDPOINT'),
		region: env.S3_REGION || 'us-east-1',
		forcePathStyle: true,
		credentials: {
			accessKeyId: requireEnv('S3_ACCESS_KEY'),
			secretAccessKey: requireEnv('S3_SECRET_KEY')
		},
		// Only send/verify checksums where S3 requires them; not every
		// S3-compatible store accepts the SDK's default CRC headers.
		requestChecksumCalculation: 'WHEN_REQUIRED',
		responseChecksumValidation: 'WHEN_REQUIRED'
	}));
}

function bucket(): string {
	return requireEnv('S3_BUCKET');
}

/** The streamed body went past the caller's size limit. */
export class UploadTooLargeError extends Error {}

export const sourceKey = (id: string) => `videos/${id}/source`;
export const outputKey = (id: string, path: string) => `videos/${id}/output/${path}`;
export const outputPrefix = (id: string) => `videos/${id}/output/`;
export const videoPrefix = (id: string) => `videos/${id}/`;

/** Fails when the bucket is unreachable or the credentials are wrong (readiness). */
export async function checkBucket(): Promise<void> {
	await client().send(new HeadBucketCommand({ Bucket: bucket() }));
}

/**
 * Stream a request body into the bucket as a multipart upload, without
 * buffering it in memory. Throws UploadTooLargeError if the body exceeds `maxBytes`.
 * Returns the number of bytes stored.
 */
export async function uploadStream(
	key: string,
	body: ReadableStream<Uint8Array>,
	contentType: string,
	maxBytes: number
): Promise<number> {
	let size = 0;
	const counted = body.pipeThrough(
		new TransformStream<Uint8Array, Uint8Array>({
			transform(chunk, controller) {
				size += chunk.byteLength;
				if (size > maxBytes)
					controller.error(new UploadTooLargeError('Upload exceeds the size limit'));
				else controller.enqueue(chunk);
			}
		})
	);
	await new Upload({
		client: client(),
		params: {
			Bucket: bucket(),
			Key: key,
			Body: Readable.fromWeb(counted as import('node:stream/web').ReadableStream),
			ContentType: contentType
		}
	}).done();
	return size;
}

export async function putObject(key: string, bytes: Uint8Array, contentType: string) {
	await client().send(
		new PutObjectCommand({ Bucket: bucket(), Key: key, Body: bytes, ContentType: contentType })
	);
}

export type StoredObject = {
	body: ReadableStream;
	contentType: string;
	contentLength: number | undefined;
};

/** Open an object for streaming, or null if it doesn't exist. */
export async function getObject(key: string): Promise<StoredObject | null> {
	try {
		const object = await client().send(new GetObjectCommand({ Bucket: bucket(), Key: key }));
		if (!object.Body) return null;
		return {
			body: object.Body.transformToWebStream(),
			contentType: object.ContentType || 'application/octet-stream',
			contentLength: object.ContentLength
		};
	} catch (error) {
		if (error instanceof NoSuchKey) return null;
		throw error;
	}
}

/** Delete every object under a prefix. */
export async function deletePrefix(prefix: string): Promise<void> {
	let token: string | undefined;
	do {
		const page = await client().send(
			new ListObjectsV2Command({ Bucket: bucket(), Prefix: prefix, ContinuationToken: token })
		);
		const keys = (page.Contents ?? []).flatMap((object) => (object.Key ? [object.Key] : []));
		// Single-object deletes: DeleteObjects needs a Content-MD5/checksum that
		// S3-compatible stores handle unevenly. A few concurrent requests at a time.
		for (let i = 0; i < keys.length; i += 32) {
			await Promise.all(
				keys
					.slice(i, i + 32)
					.map((Key) => client().send(new DeleteObjectCommand({ Bucket: bucket(), Key })))
			);
		}
		token = page.IsTruncated ? page.NextContinuationToken : undefined;
	} while (token);
}
