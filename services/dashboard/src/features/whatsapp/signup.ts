/**
 * Meta's Embedded Signup (v4), the browser's half (TV6). Two things arrive separately and both are
 * needed: the code `FB.login` returns, and the session info Meta posts to the window
 * (`{type: "WA_EMBEDDED_SIGNUP", event, data: {phone_number_id, waba_id}}`). The session info is
 * trusted only when it comes from `https://www.facebook.com`. When both are in, the dashboard sends
 * them to the engine, which does the rest (code exchange, registering the number, subscribing to
 * its messages). Shapes: the epic's "Confirmed shapes (T1)".
 *
 * Meta's SDK (`connect.facebook.net`) is loaded only by the sign-up button on the WhatsApp page and
 * removed again when it goes away: no other page ever talks to Facebook.
 */
export const FACEBOOK_ORIGIN = "https://www.facebook.com";
export const SDK_URL = "https://connect.facebook.net/en_US/sdk.js";
export const SDK_SCRIPT_ID = "facebook-jssdk";

export type SignupEvent =
	| { kind: "finish"; phoneNumberId: string; wabaId: string; coexistence: boolean }
	| { kind: "cancel" }
	| { kind: "error" }
	/** A finish with no phone number to connect (for example FINISH_ONLY_WABA). */
	| { kind: "incomplete" };

const FINISH_EVENTS = new Set([
	"FINISH",
	"FINISH_ONLY_WABA",
	"FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING",
	"FINISH_OBO_MIGRATION",
	"FINISH_GRANT_ONLY_API_ACCESS",
]);

const isId = (v: unknown): v is string => typeof v === "string" && /^\d{5,20}$/.test(v);

/** Read a `message` event from the window: null for anything that is not Meta's sign-up report. */
export function parseSignupMessage(origin: string, raw: unknown): SignupEvent | null {
	if (origin !== FACEBOOK_ORIGIN) return null;
	let data: unknown = raw;
	if (typeof raw === "string") {
		try {
			data = JSON.parse(raw);
		} catch {
			return null;
		}
	}
	if (!data || typeof data !== "object") return null;
	const msg = data as { type?: unknown; event?: unknown; data?: unknown };
	if (msg.type !== "WA_EMBEDDED_SIGNUP" || typeof msg.event !== "string") return null;
	if (msg.event === "CANCEL") return { kind: "cancel" };
	if (msg.event === "ERROR") return { kind: "error" };
	if (!FINISH_EVENTS.has(msg.event)) return null;
	const body = (msg.data ?? {}) as { phone_number_id?: unknown; waba_id?: unknown };
	if (!isId(body.phone_number_id) || !isId(body.waba_id)) return { kind: "incomplete" };
	return {
		kind: "finish",
		phoneNumberId: body.phone_number_id,
		wabaId: body.waba_id,
		coexistence: msg.event === "FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING",
	};
}

export interface SignupResult {
	code: string;
	phoneNumberId: string;
	wabaId: string;
	coexistence: boolean;
}

/** How long one half may wait for the other before the attempt is given up. */
export const STALL_MS = 60_000;

/**
 * Joins the code and the session info, in either order, and reports once. A cancel or an error
 * from the window ends the attempt (a code that arrives afterwards is ignored), and so does a
 * finish with no phone number, or one half that never gets its other half (`stallMs`): the
 * person is told instead of watching a spinner. Coexistence is whatever Meta's own event says.
 */
export function createSignupCollector(handlers: {
	onComplete: (r: SignupResult) => void;
	onCancel: () => void;
	onError: () => void;
	onIncomplete: () => void;
	stallMs?: number;
}) {
	let code: string | null = null;
	let session: { phoneNumberId: string; wabaId: string; coexistence: boolean } | null = null;
	let done = false;
	let timer: ReturnType<typeof setTimeout> | null = null;
	const stop = () => {
		if (timer !== null) clearTimeout(timer);
		timer = null;
	};
	const finish = () => {
		done = true;
		stop();
	};
	const settle = () => {
		if (done) return;
		if (code === null || session === null) {
			if (timer === null && (code !== null || session !== null)) {
				timer = setTimeout(() => {
					if (done) return;
					finish();
					handlers.onIncomplete();
				}, handlers.stallMs ?? STALL_MS);
			}
			return;
		}
		finish();
		handlers.onComplete({ code, ...session });
	};
	return {
		setCode(value: string) {
			if (!done && value) {
				code = value;
				settle();
			}
		},
		setSession(event: SignupEvent) {
			if (done) return;
			if (event.kind === "cancel") {
				finish();
				handlers.onCancel();
			} else if (event.kind === "error") {
				finish();
				handlers.onError();
			} else if (event.kind === "incomplete") {
				finish();
				handlers.onIncomplete();
			} else {
				session = { phoneNumberId: event.phoneNumberId, wabaId: event.wabaId, coexistence: event.coexistence };
				settle();
			}
		},
		/** The login window closed without a code (the person backed out). */
		abandon() {
			if (!done && code === null) {
				finish();
				handlers.onCancel();
			}
		},
		/** The page is being left: no report after this. */
		dispose() {
			finish();
		},
	};
}

/** The slice of Facebook's SDK this page uses. */
export interface FacebookSdk {
	init: (o: { appId: string; autoLogAppEvents: boolean; xfbml: boolean; version: string }) => void;
	login: (
		callback: (response: { authResponse?: { code?: string } | null }) => void,
		options: Record<string, unknown>,
	) => void;
}

declare global {
	interface Window {
		FB?: FacebookSdk;
		fbAsyncInit?: () => void;
	}
}

/** Add Meta's SDK script (once) and resolve with `FB` when it is ready. Rejects if it cannot load. */
export function loadFacebookSdk(appId: string, version: string, doc: Document = document): Promise<FacebookSdk> {
	const win = doc.defaultView as Window | null;
	if (!win) return Promise.reject(new Error("no window"));
	if (win.FB && doc.getElementById(SDK_SCRIPT_ID)) return Promise.resolve(win.FB);
	return new Promise((resolve, reject) => {
		win.fbAsyncInit = () => {
			const fb = win.FB;
			if (!fb) {
				reject(new Error("Facebook SDK did not start"));
				return;
			}
			fb.init({ appId, autoLogAppEvents: true, xfbml: false, version });
			resolve(fb);
		};
		if (doc.getElementById(SDK_SCRIPT_ID)) return;
		const script = doc.createElement("script");
		script.id = SDK_SCRIPT_ID;
		script.src = SDK_URL;
		script.async = true;
		script.defer = true;
		script.crossOrigin = "anonymous";
		script.onerror = () => reject(new Error("Facebook SDK did not load"));
		doc.body.appendChild(script);
	});
}

/** Take the script and the global away again (the person left the WhatsApp page). */
export function unloadFacebookSdk(doc: Document = document): void {
	doc.getElementById(SDK_SCRIPT_ID)?.remove();
	const win = doc.defaultView as Window | null;
	if (win) {
		win.fbAsyncInit = undefined;
		win.FB = undefined;
	}
}

/** The options `FB.login` takes for Embedded Signup (config id from the engine, a code response). */
export function loginOptions(configId: string, coexistence: boolean): Record<string, unknown> {
	return {
		config_id: configId,
		response_type: "code",
		override_default_response_type: true,
		extras: {
			setup: {},
			feature: "whatsapp_embedded_signup",
			sessionInfoVersion: "3",
			...(coexistence ? { featureType: "whatsapp_business_app_onboarding" } : {}),
		},
	};
}
