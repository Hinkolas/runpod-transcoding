import { json, type RequestHandler } from '@sveltejs/kit';
import { deleteVideo, getVideo } from '$lib/server/store';
import { refreshJob } from '$lib/server/runpod';

/** Fetch a single video, refreshing its job status if it is in-flight. */
export const GET: RequestHandler = async ({ params }) => {
	let video = getVideo(params.id!);
	if (!video) return json({ message: 'Video not found' }, { status: 404 });
	if (video.runpodJobId && (video.status === 'queued' || video.status === 'processing')) {
		await refreshJob(video);
		video = getVideo(params.id!);
	}
	return json({ video });
};

export const DELETE: RequestHandler = ({ params }) => {
	const removed = deleteVideo(params.id!);
	return new Response(null, { status: removed ? 204 : 404 });
};
