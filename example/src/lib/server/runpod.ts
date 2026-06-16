// Talks to the RunPod serverless endpoint and translates job state back into our
// in-memory store. The worker uses HTTP transport: it downloads the source from
// this server and uploads the HLS output back to this server, so the only thing
// we send RunPod is a job describing those URLs.

import { env } from '$env/dynamic/private';
import type { ProbeInfo, RenditionOutput, VideoRecord, VideoStatus } from '$lib/types';
import { updateVideo } from '$lib/server/store';

function requireEnv(name: string): string {
	const value = env[name];
	if (!value) throw new Error(`${name} is not set`);
	return value;
}

/**
 * Base URL the *worker* uses to reach this server. Must be publicly reachable by
 * RunPod's cloud workers — set PUBLIC_BASE_URL to your public/tunnel URL. Falls
 * back to the request origin (only works if that origin is already public).
 */
export function publicBaseUrl(requestUrl: URL): string {
	return (env.PUBLIC_BASE_URL || requestUrl.origin).replace(/\/$/, '');
}

/** Identifies which app/customer a job belongs to, for usage attribution. */
export function appId(): string {
	return env.APP_ID || 'demo-ui';
}

/** Build the worker job `input` with HTTP source + destination pointing back here. */
export function buildTranscodeInput(video: VideoRecord, baseUrl: string, token: string) {
	if (!video.settings) throw new Error('Video has no transcode settings');
	const auth = { Authorization: `Bearer ${token}` };
	return {
		appVideoId: video.id,
		// Echoed back in the result so each job is attributable to an app.
		metadata: { app: appId() },
		source: {
			type: 'http',
			url: `${baseUrl}/api/files/${video.id}/source`,
			headers: auth
		},
		destination: {
			type: 'http',
			baseUrl: `${baseUrl}/api/files/${video.id}/output`,
			headers: auth
		},
		renditions: video.settings.renditions,
		segmentSeconds: video.settings.segmentSeconds,
		allowUpscale: video.settings.allowUpscale,
		threads: video.settings.threads
	};
}

function mapStatus(runpodStatus: string): VideoStatus {
	switch (runpodStatus) {
		case 'IN_QUEUE':
			return 'queued';
		case 'IN_PROGRESS':
			return 'processing';
		case 'COMPLETED':
			return 'ready';
		case 'FAILED':
		case 'CANCELLED':
		case 'TIMED_OUT':
			return 'error';
		default:
			return 'processing';
	}
}

export type SubmitResult = { id: string; status: VideoStatus };

export async function submitJob(
	video: VideoRecord,
	baseUrl: string,
	token: string
): Promise<SubmitResult> {
	const endpointId = requireEnv('RUNPOD_ENDPOINT_ID');
	const apiKey = requireEnv('RUNPOD_API_KEY');
	const executionTimeout = Number(env.RUNPOD_EXECUTION_TIMEOUT_MS || 3_600_000);
	const ttl = Number(env.RUNPOD_TTL_MS || 86_400_000);

	const response = await fetch(`https://api.runpod.ai/v2/${endpointId}/run`, {
		method: 'POST',
		headers: { authorization: `Bearer ${apiKey}`, 'content-type': 'application/json' },
		body: JSON.stringify({
			input: buildTranscodeInput(video, baseUrl, token),
			policy: { executionTimeout, ttl }
		})
	});

	if (!response.ok) {
		throw new Error(`RunPod submit failed (${response.status}): ${await response.text()}`);
	}

	const data = (await response.json()) as { id?: string; status?: string };
	if (!data.id) throw new Error('RunPod did not return a job id');
	return { id: data.id, status: mapStatus(data.status || 'IN_QUEUE') };
}

type RunpodStatusResponse = {
	status?: string;
	delayTime?: number; // ms spent queued (not billed)
	executionTime?: number; // ms of compute RunPod bills for
	output?: {
		probe?: ProbeInfo;
		durationMs?: number;
		metadata?: Record<string, unknown>;
		renditions?: { label: string; height: number; width: number | null; bandwidth: number }[];
	};
	error?: string;
};

/** Poll one job's status and fold the result into the store. */
export async function refreshJob(video: VideoRecord): Promise<void> {
	if (!video.runpodJobId) return;
	// Throttle: mark polled up-front so concurrent requests don't stampede RunPod.
	updateVideo(video.id, { lastPolledAt: Date.now() });

	let endpointId: string;
	let apiKey: string;
	try {
		endpointId = requireEnv('RUNPOD_ENDPOINT_ID');
		apiKey = requireEnv('RUNPOD_API_KEY');
	} catch {
		return; // not configured — nothing to poll
	}

	let data: RunpodStatusResponse;
	try {
		const response = await fetch(
			`https://api.runpod.ai/v2/${endpointId}/status/${video.runpodJobId}`,
			{ headers: { authorization: `Bearer ${apiKey}` } }
		);
		if (!response.ok) return;
		data = (await response.json()) as RunpodStatusResponse;
	} catch {
		return; // transient network error — try again next poll
	}

	// RunPod's own timing — `executionTime` is the billed compute, captured for
	// usage attribution. Present on terminal states.
	const timing = {
		executionTimeMs: data.executionTime ?? null,
		delayTimeMs: data.delayTime ?? null
	};

	const status = mapStatus(data.status || 'IN_PROGRESS');
	if (status === 'ready') {
		const renditionsOut: RenditionOutput[] | null =
			data.output?.renditions?.map((r) => ({
				label: r.label,
				height: r.height,
				width: r.width,
				bandwidth: r.bandwidth
			})) ?? null;
		updateVideo(video.id, {
			status: 'ready',
			probe: data.output?.probe ?? null,
			durationMs: data.output?.durationMs ?? null,
			metadata: data.output?.metadata ?? null,
			renditionsOut,
			token: null, // job done — revoke the source/output capability
			...timing
		});
	} else if (status === 'error') {
		updateVideo(video.id, {
			status: 'error',
			error: data.error || `Job ${data.status?.toLowerCase() || 'failed'}`,
			token: null,
			...timing
		});
	} else {
		updateVideo(video.id, { status });
	}
}
