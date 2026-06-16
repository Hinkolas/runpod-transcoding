import type { PageServerLoad } from './$types';
import { listVideos } from '$lib/server/store';

export const load: PageServerLoad = () => ({ videos: listVideos() });
