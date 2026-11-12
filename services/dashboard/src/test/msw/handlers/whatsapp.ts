import { HttpResponse, http } from "msw";
import { BASE } from "../data";
import { coded } from "./auth";
import { INFO } from "./identity";

/**
 * TV6 handlers: a knowledge base's WhatsApp number and its chats. Stateful within a test (reset in
 * setup.ts), with the engine's rules: a token never comes back (only its last four characters),
 * a reply outside the 24-hour window is `whatsapp_window_closed`, sending a reply pauses the bot
 * for the number's pause hours, a number can be on one knowledge base only, a failing sign-up step
 * is a 502 naming it, and nothing is saved then. Indian demo data: Shree Dental Clinic, Pune.
 */
export const THREAD_OPEN = "88888888-8888-4888-8888-888888888881";
export const THREAD_OLD = "88888888-8888-4888-8888-888888888882";

interface Message {
	id: string;
	role: "user" | "assistant" | "staff";
	content: string;
	created_at: string;
}
interface Thread {
	id: string;
	name_masked: string;
	contact: string;
	profile_name: string | null;
	last_inbound_at: string | null;
	unread_count: number;
	bot_paused_until: string | null;
	paused_by: "echo" | "dashboard" | null;
	opted_out: boolean;
	window_open: boolean;
	messages: Message[];
}

const now = () => new Date().toISOString();
const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

function seedThreads(): Thread[] {
	return [
		{
			id: THREAD_OPEN,
			name_masked: "Asha K.",
			contact: "+919822012345",
			profile_name: "Asha Kulkarni",
			last_inbound_at: minutesAgo(12),
			unread_count: 2,
			bot_paused_until: null,
			paused_by: null,
			opted_out: false,
			window_open: true,
			messages: [
				{ id: "m1", role: "user", content: "Root canal ka kitna charge hai?", created_at: minutesAgo(20) },
				{
					id: "m2",
					role: "assistant",
					content: "A root canal costs Rs 6,000 to Rs 8,000.",
					created_at: minutesAgo(19),
				},
				{ id: "m3", role: "user", content: "Aur kab khula hai clinic?", created_at: minutesAgo(12) },
			],
		},
		{
			id: THREAD_OLD,
			name_masked: "•••• 4411",
			contact: "+919811144411",
			profile_name: null,
			last_inbound_at: new Date(Date.now() - 30 * 3600_000).toISOString(),
			unread_count: 0,
			bot_paused_until: null,
			paused_by: null,
			opted_out: true,
			window_open: false,
			messages: [{ id: "m9", role: "user", content: "STOP", created_at: minutesAgo(30 * 60) }],
		},
	];
}

export const whatsappState = {
	connected: false,
	status: "active" as "active" | "pending" | "error" | "disabled",
	setup: "embedded" as "embedded" | "manual",
	meta_billing: "business" as "business" | "deployer",
	coexistence: false,
	display_phone: "+91 98200 11111",
	token_hint: "9z8y",
	settings: {
		human_pause_hours: 12,
		follow_up: {
			enabled: false,
			template_name: null as string | null,
			language: null as string | null,
			after_hours: 24,
		},
		shareable_document_ids: [] as string[],
	},
	available: true,
	embeddedSignup: true,
	/** The step a sign-up fails at (a 502 naming it), or null. */
	failStep: null as string | null,
	/** Another knowledge base already has the number (409). */
	numberInUse: false,
	checkOk: true,
	threads: seedThreads(),
	sent: [] as { method: string; path: string; body: unknown }[],
};

export function resetWhatsappState(): void {
	whatsappState.connected = false;
	whatsappState.status = "active";
	whatsappState.setup = "embedded";
	whatsappState.meta_billing = "business";
	whatsappState.coexistence = false;
	whatsappState.token_hint = "9z8y";
	whatsappState.settings = {
		human_pause_hours: 12,
		follow_up: { enabled: false, template_name: null, language: null, after_hours: 24 },
		shareable_document_ids: [],
	};
	whatsappState.available = true;
	whatsappState.embeddedSignup = true;
	whatsappState.failStep = null;
	whatsappState.numberInUse = false;
	whatsappState.checkOk = true;
	whatsappState.threads = seedThreads();
	whatsappState.sent = [];
}

/** `/info` with WhatsApp on for this engine, for a test that needs the menu item and the page. */
export const infoWithWhatsapp = (over: { enabled?: boolean; available?: boolean; embedded_signup?: boolean } = {}) =>
	http.get(`${BASE}/api/v1/info`, () =>
		HttpResponse.json({
			...INFO,
			features: {
				...INFO.features,
				whatsapp: { enabled: true, available: true, embedded_signup: true, ...over },
			},
		}),
	);

function out() {
	const s = whatsappState;
	const base = {
		available: s.available,
		embedded_signup:
			s.available && s.embeddedSignup ? { app_id: "123456789", config_id: "99999", graph_version: "v23.0" } : null,
	};
	if (!s.connected) return { ...base, settings: s.settings };
	return {
		...base,
		status: s.status,
		display_phone: s.display_phone,
		verified_name: "Shree Dental Clinic",
		setup: s.setup,
		coexistence: s.coexistence,
		meta_billing: s.meta_billing,
		settings: s.settings,
		token_hint: s.token_hint,
		last_error_code: s.status === "error" ? "token" : null,
		last_inbound_at: minutesAgo(12),
	};
}

const item = (t: Thread) => ({
	id: t.id,
	name_masked: t.name_masked,
	last_inbound_at: t.last_inbound_at,
	unread_count: t.unread_count,
	bot_paused_until: t.bot_paused_until,
	opted_out: t.opted_out,
	window_open: t.window_open,
});

const record = (method: string, path: string, body: unknown) => whatsappState.sent.push({ method, path, body });
const find = (id: string | readonly string[] | undefined) => whatsappState.threads.find((t) => t.id === id);

export const whatsappHandlers = [
	http.get(`${BASE}/api/v1/corpora/:kb/whatsapp`, () => HttpResponse.json(out())),
	http.post(`${BASE}/api/v1/corpora/:kb/whatsapp/embedded-signup`, async ({ request }) => {
		const body = (await request.json()) as {
			code: string;
			phone_number_id: string;
			waba_id: string;
			coexistence?: boolean;
		};
		record("POST", "/embedded-signup", body);
		if (!/^\d{5,20}$/.test(body.phone_number_id) || !/^\d{5,20}$/.test(body.waba_id)) {
			return coded(422, "whatsapp_id_invalid");
		}
		if (whatsappState.numberInUse) return coded(409, "whatsapp_number_in_use");
		if (whatsappState.failStep) {
			return HttpResponse.json(
				{
					detail: "Meta did not complete the WhatsApp sign-up.",
					code: "whatsapp_signup_failed",
					step: whatsappState.failStep,
				},
				{ status: 502 },
			);
		}
		Object.assign(whatsappState, {
			connected: true,
			status: "active",
			setup: "embedded",
			meta_billing: "business",
			coexistence: !!body.coexistence,
		});
		return HttpResponse.json(out(), { status: 201 });
	}),
	http.put(`${BASE}/api/v1/corpora/:kb/whatsapp/manual`, async ({ request }) => {
		const body = (await request.json()) as { access_token: string; meta_billing: "business" | "deployer" };
		record("PUT", "/manual", body);
		if (body.access_token.includes("bad")) return coded(422, "whatsapp_token_invalid");
		if (whatsappState.numberInUse) return coded(409, "whatsapp_number_in_use");
		Object.assign(whatsappState, {
			connected: true,
			status: "active",
			setup: "manual",
			meta_billing: body.meta_billing,
			token_hint: body.access_token.slice(-4),
		});
		return HttpResponse.json(out());
	}),
	http.patch(`${BASE}/api/v1/corpora/:kb/whatsapp`, async ({ request }) => {
		const body = (await request.json()) as {
			enabled?: boolean | null;
			human_pause_hours?: number | null;
			follow_up?: {
				enabled?: boolean | null;
				template_name?: string | null;
				language?: string | null;
				after_hours?: number | null;
			} | null;
		};
		record("PATCH", "", body);
		const s = whatsappState.settings;
		const merged = {
			...s.follow_up,
			...Object.fromEntries(Object.entries(body.follow_up ?? {}).filter(([, v]) => v != null)),
		};
		if (merged.enabled && !(merged.template_name && merged.language)) {
			return coded(422, "whatsapp_follow_up_incomplete");
		}
		if (body.human_pause_hours != null) s.human_pause_hours = body.human_pause_hours;
		s.follow_up = merged as typeof s.follow_up;
		if (body.enabled != null) whatsappState.status = body.enabled ? "active" : "disabled";
		return HttpResponse.json(out());
	}),
	http.put(`${BASE}/api/v1/corpora/:kb/whatsapp/documents`, async ({ request }) => {
		const body = (await request.json()) as { document_ids: string[] };
		record("PUT", "/documents", body);
		whatsappState.settings.shareable_document_ids = body.document_ids;
		return HttpResponse.json(out());
	}),
	http.delete(`${BASE}/api/v1/corpora/:kb/whatsapp`, () => {
		record("DELETE", "", null);
		whatsappState.connected = false;
		return new HttpResponse(null, { status: 204 });
	}),
	http.post(`${BASE}/api/v1/corpora/:kb/whatsapp/check`, () => {
		record("POST", "/check", null);
		return HttpResponse.json(
			whatsappState.checkOk
				? { ok: true, display_phone: whatsappState.display_phone, quality_rating: "GREEN" }
				: { ok: false, error_code: "token_invalid" },
		);
	}),
	http.get(`${BASE}/api/v1/corpora/:kb/whatsapp/threads`, ({ request }) => {
		const unread = new URL(request.url).searchParams.get("unread") === "true";
		const items = whatsappState.threads.filter((t) => !unread || t.unread_count > 0).map(item);
		return HttpResponse.json({ items, total: items.length });
	}),
	http.get(`${BASE}/api/v1/corpora/:kb/whatsapp/threads/:id`, ({ params }) => {
		const t = find(params.id);
		if (!t) return coded(404, "whatsapp_thread_not_found");
		t.unread_count = 0;
		return HttpResponse.json({
			...item(t),
			unread_count: 0,
			contact: t.contact,
			profile_name: t.profile_name,
			paused_by: t.paused_by,
			messages: t.messages,
		});
	}),
	http.post(`${BASE}/api/v1/corpora/:kb/whatsapp/threads/:id/reply`, async ({ params, request }) => {
		const body = (await request.json()) as { text: string };
		record("POST", "/reply", body);
		const t = find(params.id);
		if (!t) return coded(404, "whatsapp_thread_not_found");
		if (!t.window_open) return coded(409, "whatsapp_window_closed");
		const message: Message = {
			id: `m${t.messages.length + 100}`,
			role: "staff",
			content: body.text,
			created_at: now(),
		};
		t.messages.push(message);
		t.bot_paused_until = new Date(Date.now() + whatsappState.settings.human_pause_hours * 3600_000).toISOString();
		t.paused_by = "dashboard";
		return HttpResponse.json(
			{
				message_id: message.id.padEnd(36, "0").replace(/^(.{8})(.{4})(.{4})(.{4})(.{12}).*$/, "$1-$2-$3-$4-$5"),
				whatsapp_message_id: "wamid.OUT1",
			},
			{ status: 201 },
		);
	}),
	http.patch(`${BASE}/api/v1/corpora/:kb/whatsapp/threads/:id`, async ({ params, request }) => {
		const body = (await request.json()) as { bot_paused: boolean };
		record("PATCH", "/thread", body);
		const t = find(params.id);
		if (!t) return coded(404, "whatsapp_thread_not_found");
		t.bot_paused_until = body.bot_paused
			? new Date(Date.now() + whatsappState.settings.human_pause_hours * 3600_000).toISOString()
			: null;
		t.paused_by = body.bot_paused ? "dashboard" : null;
		return HttpResponse.json(item(t));
	}),
];
