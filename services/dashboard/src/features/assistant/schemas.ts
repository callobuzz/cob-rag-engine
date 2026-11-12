import { z } from "zod";
import { isAvatarPath } from "@/lib/avatar"; // BIZ4
import { EMAIL_MESSAGES, emailSchema } from "@/lib/forms/email-rule";

/**
 * The assistant configuration in zod, field for field and limit for limit the engine's
 * `conversation/schema.py` (every model there forbids unknown fields, so this file declares
 * exactly the same keys and a form can never add one). Section schemas validate what the forms
 * edit; `assistantConfigSchema` is all of them together and `FullConfig` its type.
 *
 * Blank optional text is kept as "" while editing; `toServerConfig` turns it into null and
 * normalises phone numbers before anything is sent (the engine is still the authority).
 */

export const LANG_KEYS = ["en", "hi", "hi-Latn", "mr", "ta", "te", "bn", "gu", "kn", "ml", "pa"] as const;
export type LangKey = (typeof LANG_KEYS)[number];
export const TEMPLATE_KEYS = [
	"clinic",
	"store",
	"coaching",
	"saas",
	"real_estate",
	"government",
	"restaurant",
	"generic_support",
] as const;
export type TemplateKey = (typeof TEMPLATE_KEYS)[number];
export const TURN_LABELS = [
	"smalltalk",
	"business_question",
	"domain_explainer",
	"off_topic",
	"buying_intent",
	"human_request",
	"abuse",
	"other",
] as const;
export type TurnLabel = (typeof TURN_LABELS)[number];

/** Loading fallbacks only; the live limits come from the engine's compile response (`useRuleBudget`). */
export const FALLBACK_RULE_LIMIT = 30;
export const FALLBACK_RULE_BUDGET = 1200;

const lang = z.enum(LANG_KEYS);
export const slug = z.string().regex(/^[a-z0-9][a-z0-9_-]{0,39}$/, "Use lowercase letters, digits, - or _ (up to 40).");

/** `LocalizedText`: per-language text, each at most `max` characters. */
export const localizedText = (max: number) =>
	z.partialRecord(lang, z.string().max(max, `At most ${max} characters.`)).default({});

// ---- phone: the engine's `normalize_phone`, ported exactly --------------------------------

/** "+919822012345" for any number the engine accepts, null for one it refuses. */
export function normalizePhone(raw: string): string | null {
	const text = raw.trim();
	if (!text) return null;
	let digits = text.replace(/\D/g, "");
	if (text.startsWith("+") && !digits.startsWith("91")) {
		return digits.length >= 8 && digits.length <= 15 ? `+${digits}` : null;
	}
	if (digits.startsWith("91") && digits.length === 12) digits = digits.slice(2);
	else if (digits.startsWith("0") && digits.length === 11) digits = digits.slice(1);
	if (digits.length === 10 && "6789".includes(digits[0] ?? "")) return `+91${digits}`;
	if (digits.length === 10 && "12345".includes(digits[0] ?? "")) return `+91${digits}`; // STD landline
	return null;
}
/** Kept for the plan's name. */
export const normalizeIndianPhone = normalizePhone;

export const PHONE_MESSAGE = "Enter a 10-digit Indian number (mobile starts 6-9) or a +country number.";

/** "+91 98220 12345" for display; other countries as stored. */
export function prettyPhone(e164: string): string {
	const m = /^\+91(\d{5})(\d{5})$/.exec(e164);
	return m ? `+91 ${m[1]} ${m[2]}` : e164;
}

const blank = (v: string | null | undefined): boolean => v == null || v.trim() === "";

/** Optional phone: blank, or a number the engine accepts. */
export const phoneField = z
	.string()
	.nullable()
	.refine((v) => blank(v) || normalizePhone(v as string) !== null, PHONE_MESSAGE);

const urlOk = (v: string, httpsOnly: boolean): boolean => {
	const t = v.trim();
	return t.length <= 2048 && (httpsOnly ? /^https:\/\//i.test(t) : /^https?:\/\//i.test(t));
};
/** Optional web address (http or https), at most 2048 characters. */
export const urlField = z
	.string()
	.nullable()
	.refine((v) => blank(v) || urlOk(v as string, false), "Use a full address starting with https://");
// BIZ4: the picture an owner uploaded is the engine's own path, not an https address
export const avatarField = z
	.string()
	.nullable()
	.refine(
		(v) => blank(v) || isAvatarPath((v as string).trim()) || urlOk(v as string, true),
		"Use an https:// image address, or upload a picture.",
	);
// /BIZ4
export const httpsField = z
	.string()
	.nullable()
	.refine((v) => blank(v) || urlOk(v as string, true), "Use an https:// image address.");

/** Optional e-mail: blank, or an address the shared rule accepts (the engine refuses reserved names). */
export const emailField = z
	.string()
	.nullable()
	.refine((v) => blank(v) || emailSchema(true).safeParse(v).success, EMAIL_MESSAGES.invalid);

// ---- business card and identity ---------------------------------------------------------------

const hhmm = z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, "Use 24-hour time, like 09:30.");
export const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
export type Day = (typeof DAYS)[number];
/** Overnight ranges (18:00 to 02:00) are allowed; equal times mean nothing. */
export const dayHours = z
	.object({ day: z.enum(DAYS), open: hhmm, close: hhmm })
	.refine((h) => h.open !== h.close, { message: "Closing time must differ from opening time.", path: ["close"] });

export const businessCardSchema = z.object({
	timezone: z.string().min(1, "Choose a time zone."),
	hours: z.array(dayHours).max(21, "At most 21 opening ranges."),
	holidays: z
		.array(z.object({ date: z.iso.date("Pick a date."), name: z.string().min(1, "Name the holiday.").max(80) }))
		.max(60),
	address: z.string().max(300).nullable(),
	phone: phoneField,
	whatsapp: phoneField,
	email: emailField,
	website: urlField,
	booking_url: urlField,
	service_areas: z.array(z.string().min(1).max(80, "At most 80 characters.")).max(20, "At most 20 areas."),
	payment_info: z.string().max(200).nullable(),
	refund_info: z.string().max(200).nullable(),
});

export const identitySchema = z.object({
	assistant_name: z.string().trim().min(1, "Give the assistant a name.").max(40),
	avatar_url: avatarField, // BIZ4
	business_name: z.string().max(120),
	business_type: z.enum(TEMPLATE_KEYS),
	business_profile: z.string().max(500, "Keep it under 500 characters: 1 to 3 factual sentences."),
	audience: z.string().max(200),
	ai_disclosure: z.boolean(),
	ai_disclosure_text: localizedText(200),
	card: businessCardSchema,
});

// ---- voice and language ------------------------------------------------------------------

export const voiceSchema = z.object({
	tone_preset: z.enum(["friendly", "professional", "warm", "matter_of_fact", "playful"]),
	custom_tone: z.string().max(300),
	formality: z.enum(["formal", "neutral", "casual"]),
	hindi_pronoun: z.enum(["aap", "tum"]),
	response_length: z.enum(["short", "medium", "detailed"]),
	format: z.object({
		lists: z.enum(["prefer", "allow", "avoid"]),
		tables: z.enum(["when_comparing", "allow", "never"]),
		headings: z.enum(["never", "allow"]),
		custom: z.string().max(200),
	}),
	emoji: z.enum(["none", "light", "frequent"]),
	preferred_terms: z
		.array(
			z.object({
				use: z.string().trim().min(1, "Write the word to use.").max(60),
				instead_of: z.string().trim().min(1, "Write the word to replace.").max(60),
			}),
		)
		.max(30),
	banned_phrases: z.array(z.string().trim().min(1, "Write the phrase.").max(80, "At most 80 characters.")).max(30),
	voice_samples: z.array(z.string().max(500, "At most 500 characters.")).max(5, "At most 5 samples."),
	greeting: localizedText(300),
	closing_line: localizedText(200),
});

export const languageSchema = z
	.object({
		primary: lang,
		supported: z.array(lang).min(1, "Pick at least one language."),
		auto_detect: z.boolean(),
		reply_policy: z.enum(["mirror", "fixed"]),
		script_policy: z.enum(["mirror", "native", "roman"]),
		documents_language: lang,
		translate_queries: z.boolean(),
	})
	.refine((l) => l.supported.includes(l.primary), {
		message: "The main language must be one of the supported languages.",
		path: ["primary"],
	});

// ---- scope ---------------------------------------------------------------------------------

export const quickReply = z.object({
	label: z.string().trim().min(1, "Write the button text.").max(20, "At most 20 characters."),
	message: z.string().trim().min(1, "Write what it sends.").max(200, "At most 200 characters."),
});

export const topicSchema = z.object({
	id: slug,
	name: z.string().trim().min(1, "Name the topic.").max(60),
	description: z.string().max(300),
	examples: z.array(z.string().max(200, "At most 200 characters.")).max(10, "At most 10 examples."),
	verbatim_answer: localizedText(2000).nullable(),
	quick_replies: z.array(quickReply).max(4, "At most 4 buttons."),
});

export const blockedTopicSchema = z.object({
	id: slug,
	name: z.string().trim().min(1, "Name the topic.").max(60),
	description: z.string().max(300),
	action: z.enum(["decline", "handoff"]),
	message: localizedText(300).nullable(),
});

export const SENSITIVE_PROFILES = ["none", "medical", "legal", "financial", "education", "government"] as const;

const score = z
	.number("Use a number from 0 to 1.")
	.min(0, "Use a number from 0 to 1.")
	.max(1, "Use a number from 0 to 1.");

export const scopeSchema = z
	.object({
		scope_mode: z.enum(["strict_docs", "docs_plus_domain", "open_with_guardrails"]),
		topics: z.array(topicSchema).max(40, "At most 40 topics."),
		blocked_topics: z.array(blockedTopicSchema).max(40, "At most 40 blocked topics."),
		sensitive_profile: z.enum(SENSITIVE_PROFILES),
		disclaimers: localizedText(400),
		fallback: z.object({
			no_answer_message: localizedText(400),
			actions: z.array(z.enum(["clarify", "handoff", "lead"])),
			softener: localizedText(300),
		}),
		min_retrieval_score: score.nullable(),
		claim_check: z.enum(["enforce", "warn", "off"]),
		max_off_topic_strikes: z.number("Use a number from 1 to 10.").int().min(1, "Use 1 to 10.").max(10, "Use 1 to 10."),
	})
	.refine(
		(s) => {
			const ids = [...s.topics.map((t) => t.id), ...s.blocked_topics.map((t) => t.id)];
			return new Set(ids).size === ids.length;
		},
		{
			message: "Two topics share the same id. Topic ids must be different, also across blocked topics.",
			path: ["topics"],
		},
	);

// ---- rules ---------------------------------------------------------------------------------

export const RULE_TYPES = ["communication", "context", "clarification", "escalation", "sales", "compliance"] as const;

export const ruleSchema = z.object({
	id: slug,
	type: z.enum(RULE_TYPES),
	when: z
		.object({
			topic: z.string().nullable(),
			label: z.enum(TURN_LABELS).nullable(),
			language: lang.nullable(),
			page_url_glob: z.string().max(200).nullable(),
			office_hours: z.enum(["open", "closed"]).nullable(),
			lead_status: z.enum(["none", "captured"]).nullable(),
		})
		.nullable(),
	instruction: z.string().trim().min(1, "Write the instruction.").max(500, "At most 500 characters."),
	enabled: z.boolean(),
	priority: z.number("Use a number from 0 to 100.").int().min(0, "Use 0 to 100.").max(100, "Use 0 to 100."),
	source: z.enum(["owner", "template", "setup"]),
});

/** The rules list without the on-count cap (the cap is the engine's, known only after a compile). */
const rulesList = z
	.array(ruleSchema)
	.max(100, "At most 100 rules.")
	.refine((rs) => new Set(rs.map((r) => r.id)).size === rs.length, { message: "Two rules have the same id." });

/** `limit` comes from the engine (`CompileResponse.max_enabled_rules`), read when validating. */
export function rulesSchemaFor(limit: number | (() => number)) {
	const max = () => (typeof limit === "function" ? limit() : limit);
	return z.object({
		rules: rulesList.refine((rs) => rs.filter((r) => r.enabled).length <= max(), {
			error: () => `Only ${max()} rules can be on at once.`,
		}),
	});
}

// ---- conversation, leads, handoff ------------------------------------------------------------

export const CTA_TYPES = ["lead_form", "whatsapp", "call", "booking", "handoff", "link", "none"] as const;

export const conversationSchema = z.object({
	starters: z.array(quickReply).max(4, "At most 4 starters."),
	ai_followups: z.boolean(),
	cta: z.object({
		max_per_answer: z.union([z.literal(0), z.literal(1)]),
		buying_intent_cta: z.enum(CTA_TYPES),
		link_url: urlField,
		link_label: localizedText(40).nullable(),
	}),
	proactive_greetings: z
		.array(
			z.object({
				id: slug,
				page_url_glob: z.string().trim().min(1, "Which pages?").max(200),
				delay_seconds: z.number("Use 0 to 300 seconds.").int().min(0, "Use 0 to 300.").max(300, "Use 0 to 300."),
				message: localizedText(200),
				frequency: z.enum(["once_per_session", "once_per_day", "always"]),
				enabled: z.boolean(),
			}),
		)
		.max(10, "At most 10 greetings."),
});

const fieldMode = z.enum(["required", "optional", "off"]);
const notifySchema = z.object({
	emails: z.array(z.string().trim().pipe(emailSchema(true))).max(5, "At most 5 addresses."),
	webhook: z.boolean(),
	daily_digest: z.boolean(),
});

export const leadsSchema = z
	.object({
		enabled: z.boolean(),
		mode: z.enum(["form", "conversational"]),
		triggers: z.object({
			buying_intent: z.boolean(),
			no_answer: z.boolean(),
			after_n_messages: z.number().int().min(1, "Use 1 to 50.").max(50, "Use 1 to 50.").nullable(),
			topics: z.array(z.string()),
			page_url_globs: z.array(z.string().max(200)).max(10, "At most 10 pages."),
			human_request: z.boolean(),
			out_of_hours: z.boolean(),
		}),
		name: fieldMode,
		phone: fieldMode,
		email: fieldMode,
		custom_fields: z
			.array(
				z
					.object({
						id: slug,
						label: localizedText(60),
						type: z.enum(["text", "select", "date", "number"]),
						required: z.boolean(),
						options: z.array(z.string().min(1).max(60, "At most 60 characters.")).max(20),
						max_length: z.number("Use 1 to 200.").int().min(1, "Use 1 to 200.").max(200, "Use 1 to 200."),
					})
					.refine((f) => f.type !== "select" || f.options.length > 0, {
						message: "A choice field needs at least one choice.",
						path: ["options"],
					}),
			)
			.max(6, "At most 6 extra fields."),
		consent: z.object({
			notice: localizedText(1500),
			purpose: z.string().trim().min(1, "Say what the details are used for.").max(300),
			retention_days: z.number("Use 30 to 3650.").int().min(30, "Use 30 to 3650.").max(3650, "Use 30 to 3650."),
			privacy_policy_url: urlField,
			notice_version: z.number().int().min(1),
		}),
		/** Rules for marking a lead qualified (the Leads section edits them). */
		qualification: z
			.array(
				z.object({
					status: z.enum(["qualified", "partial"]),
					conditions: z
						.array(
							z
								.object({
									field: z.string().min(1).max(40),
									op: z.enum(["eq", "neq", "in", "gte", "lte", "present"]),
									value: z
										.union([z.string(), z.number(), z.array(z.string())])
										.nullable()
										.default(null),
								})
								.refine(
									(c) =>
										c.op === "present" ||
										(Array.isArray(c.value) ? c.value.length > 0 : String(c.value ?? "").trim() !== ""),
									{ message: "Enter a value.", path: ["value"] },
								)
								.refine(
									(c) =>
										(c.op !== "gte" && c.op !== "lte") ||
										(c.value !== null && c.value !== "" && Number.isFinite(Number(c.value))),
									{ message: "Enter a number.", path: ["value"] },
								),
						)
						.min(1, "A rule needs at least one condition.")
						.max(5),
				}),
			)
			.max(10, "At most 10 rules."),
		notify: notifySchema,
		confirmation_message: localizedText(300),
	})
	.refine((l) => l.phone === "required" || l.email === "required", {
		message: "Phone or e-mail must be required.",
		path: ["phone"],
	});

export const HANDOFF_KINDS = ["whatsapp", "call", "callback", "email", "contact_page", "booking"] as const;

export const handoffSchema = z.object({
	enabled: z.boolean(),
	channels: z
		.array(
			z.object({
				kind: z.enum(HANDOFF_KINDS),
				enabled: z.boolean(),
				label: localizedText(40).nullable(),
				value: z.string().max(2048).nullable(),
				available: z.enum(["always", "open_hours", "closed_hours"]),
			}),
		)
		.max(6, "At most 6 ways to reach you."),
	triggers: z.object({
		explicit_request: z.boolean(),
		frustration: z.boolean(),
		unresolved_turns: z.number("Use 1 to 20.").int().min(1, "Use 1 to 20.").max(20, "Use 1 to 20.").nullable(),
		low_confidence_streak: z.number("Use 1 to 10.").int().min(1, "Use 1 to 10.").max(10, "Use 1 to 10.").nullable(),
		blocked_topics: z.boolean(),
		keywords: z.array(z.string().trim().min(1)).max(30, "At most 30 keywords."),
		out_of_hours: z.boolean(),
		// TV1 human: offer a person after this many questions it could not answer (0 = never by itself)
		offer_after_misses: z.number("Use 0 to 5.").int().min(0, "Use 0 to 5.").max(5, "Use 0 to 5."),
		// /TV1 human
	}),
	// TV1 human: the "Talk to a person" button of the widget
	show_human_button: z.boolean(),
	// /TV1 human
	// PIP1 lane: shown when the engine is too busy to answer in time
	messages: z.object({
		open: localizedText(300),
		closed: localizedText(300),
		paused: localizedText(300),
		busy: localizedText(300),
	}),
	// /PIP1 lane
	notify: notifySchema,
});

export const advancedSchema = z.object({
	extra_instructions: z.string().max(15_000, "At most 15,000 characters.").nullable(),
});

// ---- the whole configuration --------------------------------------------------------------

/** `rules` is a list at the top level; its form wraps it (`rulesSchemaFor`). */
export const assistantConfigSchema = z.object({
	schema_version: z.literal(1),
	enabled: z.boolean(),
	identity: identitySchema,
	voice: voiceSchema,
	language: languageSchema,
	scope: scopeSchema,
	rules: rulesList,
	conversation: conversationSchema,
	leads: leadsSchema,
	handoff: handoffSchema,
	advanced: advancedSchema,
});

export type FullConfig = z.infer<typeof assistantConfigSchema>;
export type ConfigSection = Exclude<keyof FullConfig, "schema_version" | "enabled">;

const nullIfBlank = (v: string | null | undefined): string | null => (v == null || v.trim() === "" ? null : v.trim());

/** Clean a config for sending: blank optional text becomes null, phone numbers are normalised. */
export function toServerConfig(config: FullConfig): FullConfig {
	const card = config.identity.card;
	const phone = (v: string | null) => {
		const t = nullIfBlank(v);
		return t == null ? null : (normalizePhone(t) ?? t);
	};
	const local = <T extends Record<string, string | undefined>>(v: T | null): T | null => {
		if (v == null) return null;
		const out = Object.fromEntries(Object.entries(v).filter(([, x]) => (x ?? "").trim() !== "")) as T;
		return Object.keys(out).length ? out : null;
	};
	return {
		...config,
		identity: {
			...config.identity,
			avatar_url: nullIfBlank(config.identity.avatar_url),
			card: {
				...card,
				address: nullIfBlank(card.address),
				phone: phone(card.phone),
				whatsapp: phone(card.whatsapp),
				email: nullIfBlank(card.email),
				website: nullIfBlank(card.website),
				booking_url: nullIfBlank(card.booking_url),
				payment_info: nullIfBlank(card.payment_info),
				refund_info: nullIfBlank(card.refund_info),
			},
		},
		scope: {
			...config.scope,
			topics: config.scope.topics.map((t) => ({ ...t, verbatim_answer: local(t.verbatim_answer) })),
			blocked_topics: config.scope.blocked_topics.map((b) => ({ ...b, message: local(b.message) })),
		},
		conversation: {
			...config.conversation,
			cta: {
				...config.conversation.cta,
				link_url: nullIfBlank(config.conversation.cta.link_url),
				link_label: local(config.conversation.cta.link_label),
			},
		},
		leads: {
			...config.leads,
			consent: { ...config.leads.consent, privacy_policy_url: nullIfBlank(config.leads.consent.privacy_policy_url) },
		},
		handoff: {
			...config.handoff,
			channels: config.handoff.channels.map((c) => ({ ...c, label: local(c.label), value: nullIfBlank(c.value) })),
		},
		advanced: {
			extra_instructions: config.advanced.extra_instructions?.trim() ? config.advanced.extra_instructions : null,
		},
	};
}
