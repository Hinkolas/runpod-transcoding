import { json, type RequestHandler } from '@sveltejs/kit';
import { clearOutputs, getVideo, updateVideo } from '$lib/server/store';
import { publicBaseUrl, submitJob } from '$lib/server/runpod';
import type { TranscodeSettings } from '$lib/types';

/** Start (or restart) a transcode for a video with the supplied settings. */
export const POST: RequestHandler = async ({ params, request, url }) => {
	const id = params.id!;
	if (!getVideo(id)) return json({ message: 'Video not found' }, { status: 404 });

	let settings: TranscodeSettings;
	try {
		settings = (await request.json()) as TranscodeSettings;
	} catch {
		return json({ message: 'Invalid JSON body' }, { status: 400 });
	}
	if (!settings?.renditions?.length) {
		return json({ message: 'At least one rendition is required' }, { status: 400 });
	}

	// Mint a fresh per-job token scoped to this video's source/output endpoints.
	const token = crypto.randomUUID();

	// Reset any prior run before queueing the new one.
	clearOutputs(id);
	updateVideo(id, {
		settings,
		status: 'queued',
		error: null,
		runpodJobId: null,
		renditionsOut: null,
		durationMs: null,
		executionTimeMs: null,
		delayTimeMs: null,
		metadata: null,
		probe: null,
		token
	});

	try {
		const video = getVideo(id)!; // now carries the new settings
		const result = await submitJob(video, publicBaseUrl(url), token);
		const updated = updateVideo(id, { runpodJobId: result.id, status: result.status });
		return json({ video: updated });
	} catch (error) {
		const message = error instanceof Error ? error.message : 'Failed to submit job';
		const updated = updateVideo(id, { status: 'error', error: message, token: null });
		return json({ message, video: updated }, { status: 500 });
	}
};
