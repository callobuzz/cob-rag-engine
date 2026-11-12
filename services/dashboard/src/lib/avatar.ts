import { currentRagUrl } from "@/lib/auth/store";

/** The engine's own path for an uploaded assistant picture (`GET /api/v1/public/avatars/<id>.png`). */
export const AVATAR_PATH =
	/^\/api\/v1\/public\/avatars\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.png$/;

export const isAvatarPath = (value: string): boolean => AVATAR_PATH.test(value);

/**
 * An address a browser can load for an assistant's picture. An uploaded picture is stored as the
 * engine's own path, which needs the engine's address in front of it (the dashboard may live on
 * another host); an https address stays as it is. Anything else is not shown.
 */
export function avatarSrc(url: string | null | undefined): string | null {
	if (!url) return null;
	if (isAvatarPath(url)) return `${currentRagUrl()}${url}`;
	return url.startsWith("https://") ? url : null;
}
