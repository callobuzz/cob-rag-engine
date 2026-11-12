/**
 * WhatsApp part of the fake engine (TV6). Follows `api/routes/whatsapp.py`: one number per
 * knowledge base, the token never comes back, a reply pauses the assistant in that chat. Indian
 * demo data: Shree Dental Clinic, Pune. Meta's own window is never opened: the spec stubs the
 * Facebook SDK.
 */
export interface WhatsappMock {
	enabled: boolean;
	connected: boolean;
	unread: number;
	paused: boolean;
	sent: { method: string; path: string; body: unknown }[];
	messages: { id: string; role: string; content: string; created_at: string }[];
}

export const createWhatsappState = (): WhatsappMock => ({
	enabled: false,
	connected: false,
	unread: 2,
	paused: false,
	sent: [],
	messages: [
		{ id: "m1", role: "user", content: "Root canal ka kitna charge hai?", created_at: "2026-10-05T08:40:00Z" },
		{
			id: "m2",
			role: "assistant",
			content: "A root canal costs Rs 6,000 to Rs 8,000.",
			created_at: "2026-10-05T08:40:05Z",
		},
	],
});

export interface WhatsappCtx {
	isAdmin: boolean;
	reply: (status: number, body?: unknown) => Promise<void>;
}

const SETTINGS = {
	human_pause_hours: 12,
	follow_up: { enabled: false, template_name: null, language: null, after_hours: 24 },
	shareable_document_ids: [],
};
const THREAD = "88888888-8888-4888-8888-888888888881";
const PAUSED_UNTIL = "2099-01-01T00:00:00Z";

export async function handleWhatsappRoute(
	s: WhatsappMock,
	method: string,
	path: string,
	body: Record<string, unknown>,
	ctx: WhatsappCtx,
): Promise<boolean> {
	const m = /^\/api\/v1\/corpora\/[^/]+\/whatsapp(\/.*)?$/.exec(path);
	if (!m) return false;
	const sub = m[1] ?? "";
	if (method !== "GET") s.sent.push({ method, path: sub, body });
	const summary = (unread: number) => ({
		id: THREAD,
		name_masked: "Asha K.",
		last_inbound_at: new Date().toISOString(),
		unread_count: unread,
		bot_paused_until: s.paused ? PAUSED_UNTIL : null,
		opted_out: false,
		window_open: true,
	});
	if (method === "GET" && sub === "") {
		await ctx.reply(200, {
			available: s.enabled,
			embedded_signup: { app_id: "123456789", config_id: "99999", graph_version: "v23.0" },
			settings: SETTINGS,
			...(s.connected
				? {
						status: "active",
						display_phone: "+91 98200 11111",
						verified_name: "Shree Dental Clinic",
						setup: "embedded",
						coexistence: false,
						meta_billing: "business",
						token_hint: "9z8y",
						last_inbound_at: "2026-10-05T08:40:00Z",
					}
				: {}),
		});
		return true;
	}
	if (method === "POST" && sub === "/embedded-signup") {
		s.connected = true;
		await ctx.reply(201, {
			available: true,
			embedded_signup: null,
			settings: SETTINGS,
			status: "active",
			display_phone: "+91 98200 11111",
			verified_name: "Shree Dental Clinic",
			setup: "embedded",
			coexistence: false,
			meta_billing: "business",
			token_hint: "9z8y",
		});
		return true;
	}
	if (method === "GET" && sub === "/threads") {
		await ctx.reply(200, { items: [summary(s.unread)], total: 1 });
		return true;
	}
	if (method === "GET" && sub === `/threads/${THREAD}`) {
		s.unread = 0;
		await ctx.reply(200, {
			...summary(0),
			contact: "+919822012345",
			profile_name: "Asha Kulkarni",
			paused_by: s.paused ? "dashboard" : null,
			messages: s.messages,
		});
		return true;
	}
	if (method === "POST" && sub === `/threads/${THREAD}/reply`) {
		s.paused = true;
		s.messages.push({
			id: "m3",
			role: "staff",
			content: String(body.text ?? ""),
			created_at: new Date().toISOString(),
		});
		await ctx.reply(201, { message_id: "99999999-9999-4999-8999-999999999991", whatsapp_message_id: "wamid.OUT1" });
		return true;
	}
	return false;
}
