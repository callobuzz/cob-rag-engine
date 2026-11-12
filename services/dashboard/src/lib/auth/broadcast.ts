/**
 * Cross-tab sign-out: BroadcastChannel("cob-auth") message {type: "signed-out", ...} plus the
 * `storage` event when localStorage["cob.rt"] is removed ("Keep me signed in" tabs share one
 * token). A message names the session (from the refresh token) or the key kind, so signing out a
 * different session in another tab does not sign this one out.
 */

export type AuthMessage = { type: "signed-out"; sid: string | null; kind: "session" | "api_key" };

const CHANNEL = "cob-auth";
let channel: BroadcastChannel | null = null;

function getChannel(): BroadcastChannel | null {
	if (channel) return channel;
	if (typeof BroadcastChannel === "undefined") return null;
	try {
		channel = new BroadcastChannel(CHANNEL);
	} catch {
		channel = null;
	}
	return channel;
}

export function broadcastSignedOut(msg: Omit<AuthMessage, "type">): void {
	getChannel()?.postMessage({ type: "signed-out", ...msg } satisfies AuthMessage);
}

export function onAuthMessage(handler: (msg: AuthMessage) => void): () => void {
	const ch = getChannel();
	if (!ch) return () => {};
	const listener = (e: MessageEvent) => {
		const d = e.data as Partial<AuthMessage> | null;
		if (d && d.type === "signed-out") handler(d as AuthMessage);
	};
	ch.addEventListener("message", listener);
	return () => ch.removeEventListener("message", listener);
}

/** Tests only. */
export function closeBroadcast(): void {
	channel?.close();
	channel = null;
}
