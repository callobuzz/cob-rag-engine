import { HttpResponse, http } from "msw";
import type { LandingContent, LandingState } from "@/features/landing/api";
import { allTexts, missingIn } from "@/features/landing/draft";
import { BASE } from "../data";
import { coded } from "./auth";
import fixture from "./landing-default.fixture.json";

/**
 * LP4 handlers: the public website's editor on the engine's rules. A draft is saved against its
 * revision (409 `landing_revision_conflict`), switching on needs a published version and an
 * address, publishing needs every switched-on language complete (409 `landing_translation_missing`
 * with `paths`), a file in use cannot be deleted (409 `landing_asset_in_use`). Indian demo data:
 * Sharma Dental Clinic, Pune.
 */
type ThemeBody = { brand_color: string; accent_color?: string | null; base: "light" | "dark" | "auto" };
type Asset = LandingState["assets"][number];

const DEFAULT = fixture.content as unknown as LandingContent;
const DEFAULT_THEME = fixture.theme as unknown as LandingState["draft_theme"];

export const landingState = {
	draft: structuredClone(DEFAULT) as LandingContent,
	revision: 1,
	enabled: false,
	address: null as string | null,
	/** Frozen published versions, oldest first. */
	versions: [] as {
		version: number;
		published_at: string;
		published_by: string;
		change_note: string | null;
		content: LandingContent;
	}[],
	assets: [] as Asset[],
	askCorpus: null as string | null,
	askKey: null as string | null,
	askStatus: "not_connected" as LandingState["ask"]["status"],
	/** Requests the screens made, for assertions. */
	calls: [] as { method: string; path: string; body: unknown }[],
	themeCalls: [] as ThemeBody[],
	/** Set to make the next generate call fail with this status and code. */
	generateFails: null as null | { status: number; code: string },
	copyWarnings: [] as LandingState["copy_warnings"],
	/** Plans the engine says still have a placeholder price. */
	placeholderNames: [] as string[],
	/** How long a preview link lasts, in milliseconds. */
	previewTtlMs: 15 * 60_000,
};

export function resetLandingState() {
	landingState.draft = structuredClone(DEFAULT);
	landingState.revision = 1;
	landingState.enabled = false;
	landingState.address = null;
	landingState.versions = [];
	landingState.assets = [];
	landingState.askCorpus = null;
	landingState.askKey = null;
	landingState.askStatus = "not_connected";
	landingState.calls = [];
	landingState.themeCalls = [];
	landingState.generateFails = null;
	landingState.copyWarnings = [];
	landingState.placeholderNames = [];
	landingState.previewTtlMs = 15 * 60_000;
}

/** Publish the current draft as the next version (a starting point for a test). */
export function seedPublished(note: string | null = null, when = "2026-10-08T09:00:00Z") {
	landingState.versions.push({
		version: landingState.versions.length + 1,
		published_at: when,
		published_by: "sv@sahayak.example",
		change_note: note,
		content: structuredClone(landingState.draft),
	});
}

function themeFor(body: ThemeBody): LandingState["draft_theme"] {
	const theme = structuredClone(DEFAULT_THEME);
	theme.light = { ...theme.light, brand: body.brand_color };
	theme.css = theme.css.replace(/--brand:#[0-9a-fA-F]{6}/, `--brand:${body.brand_color}`);
	// a very light colour needs a darker button: the engine says so
	if (/^#[ef]/i.test(body.brand_color)) {
		theme.adjusted = ["Buttons use a darker shade of your colour so their text stays readable.", ...theme.adjusted];
	}
	return theme;
}

export function stateOut(): LandingState {
	const live = landingState.versions.at(-1);
	const address = landingState.address;
	const warnings: LandingState["warnings"] = [];
	if (address == null) warnings.push({ code: "address_missing", message: "Set your website's address." });
	if (!live) warnings.push({ code: "nothing_published", message: "Nothing is published yet." });
	if (landingState.placeholderNames.length > 0) {
		warnings.push({
			code: "placeholder_price",
			message: `These plans still have a placeholder price and will say "Price on request": ${landingState.placeholderNames.join(", ")}.`,
		});
	}
	return {
		address,
		address_effective: address,
		ask: { corpus_id: landingState.askCorpus, widget_key_id: landingState.askKey, status: landingState.askStatus },
		assets: landingState.assets,
		copy_warnings: landingState.copyWarnings,
		draft: landingState.draft,
		draft_revision: landingState.revision,
		draft_theme: themeFor({
			brand_color: landingState.draft.theme?.brand_color ?? "#0f766e",
			base: landingState.draft.theme?.base ?? "light",
		}),
		enabled: landingState.enabled,
		limits: { ...(fixture.limits as Record<string, number>), "upload.video_mb": 25, "upload.image_mb": 5 },
		published: live
			? { version: live.version, published_at: live.published_at, published_by: live.published_by }
			: null,
		warnings,
	};
}

function referenced(id: string): boolean {
	return JSON.stringify(landingState.draft).includes(id);
}

const record = async (request: Request) => {
	const text = await request.clone().text();
	landingState.calls.push({
		method: request.method,
		path: new URL(request.url).pathname,
		body: text && request.headers.get("content-type")?.includes("json") ? JSON.parse(text) : undefined,
	});
};

export const PRESETS = [
	{ key: "clinic", name: "Clinic or hospital", languages: ["en", "hi", "hi-Latn"] },
	{ key: "shop", name: "Shop or D2C brand", languages: ["en", "hi", "hi-Latn"] },
	{ key: "coaching", name: "Coaching or school", languages: ["en", "hi", "hi-Latn"] },
	{ key: "realestate", name: "Real estate", languages: ["en", "hi", "hi-Latn"] },
	{ key: "salon", name: "Salon or spa", languages: ["en", "hi", "hi-Latn"] },
	{ key: "services", name: "Services", languages: ["en", "hi", "hi-Latn"] },
];

export const SAMPLE_CONVERSATION = {
	preset: "clinic",
	assistant_name: { en: "Asha" },
	business_name: { en: "Shree Dental Clinic, Pune" },
	generated: false,
	turns: [
		{ role: "visitor", text: { en: "Is Dr. Shree available on Saturday evening?" } },
		{
			role: "assistant",
			text: { en: "Yes, Saturday 5 to 8 pm. Shall I note your number so the clinic can confirm?" },
			source: { en: "Timings page · updated 2 Oct" },
		},
	],
	lead: null,
};

export const landingHandlers = [
	http.get(`${BASE}/api/v1/admin/landing`, () => HttpResponse.json(stateOut())),
	http.put(`${BASE}/api/v1/admin/landing/draft`, async ({ request }) => {
		await record(request);
		const body = (await request.json()) as { base_revision: number; content: LandingContent };
		if (body.base_revision !== landingState.revision) return coded(409, "landing_revision_conflict");
		landingState.draft = body.content;
		landingState.revision += 1;
		return HttpResponse.json(stateOut());
	}),
	http.put(`${BASE}/api/v1/admin/landing/settings`, async ({ request }) => {
		await record(request);
		const body = (await request.json()) as { enabled?: boolean; address?: string | null };
		if ("address" in body) {
			if (body.address && !/^https:\/\/[a-z0-9.-]+(:\d+)?\/?$/i.test(body.address)) {
				return coded(422, "landing_address_invalid");
			}
			landingState.address = body.address ? body.address.replace(/\/$/, "") : null;
		}
		if (body.enabled === true) {
			if (landingState.versions.length === 0) return coded(409, "landing_nothing_published");
			if (landingState.address == null) return coded(409, "landing_needs_address");
		}
		if (body.enabled != null) landingState.enabled = body.enabled;
		return HttpResponse.json(stateOut());
	}),
	http.post(`${BASE}/api/v1/admin/landing/theme/preview`, async ({ request }) => {
		const body = (await request.json()) as ThemeBody;
		landingState.themeCalls.push(body);
		return HttpResponse.json(themeFor(body));
	}),
	http.post(`${BASE}/api/v1/admin/landing/preview-link`, async ({ request }) => {
		await record(request);
		const body = (await request.json()) as { page: string; lang: string };
		return HttpResponse.json({
			url: `${BASE}/_landing/preview/${body.page}?t=token${landingState.revision}&lang=${body.lang}`,
			expires_at: new Date(Date.now() + landingState.previewTtlMs).toISOString(),
		});
	}),
	http.post(`${BASE}/api/v1/admin/landing/publish`, async ({ request }) => {
		await record(request);
		const body = (await request.json()) as { base_revision: number; change_note?: string | null };
		if (body.base_revision !== landingState.revision) return coded(409, "landing_revision_conflict");
		for (const lang of landingState.draft.languages) {
			const gaps = missingIn(landingState.draft, lang);
			if (gaps.length > 0) {
				return coded(409, "landing_translation_missing", {
					detail: `Some texts are missing in ${lang}.`,
					paths: gaps.slice(0, 50),
					language: lang,
				});
			}
		}
		seedPublished(body.change_note ?? null, "2026-10-10T09:30:00Z");
		const made = landingState.versions.at(-1);
		if (made) made.published_by = "sv@sahayak.example";
		return HttpResponse.json(stateOut());
	}),
	http.get(`${BASE}/api/v1/admin/landing/versions`, () =>
		HttpResponse.json({
			items: [...landingState.versions].reverse().map(({ content: _c, ...v }) => v),
		}),
	),
	http.post(`${BASE}/api/v1/admin/landing/versions/:version/restore`, async ({ request, params }) => {
		await record(request);
		const body = (await request.json()) as { base_revision: number };
		if (body.base_revision !== landingState.revision) return coded(409, "landing_revision_conflict");
		const found = landingState.versions.find((v) => v.version === Number(params.version));
		if (!found) return coded(404, "landing_version_not_found");
		landingState.draft = structuredClone(found.content);
		landingState.revision += 1;
		return HttpResponse.json(stateOut());
	}),
	http.post(`${BASE}/api/v1/admin/landing/assets`, async ({ request }) => {
		const form = await request.formData();
		const kind = String(form.get("kind")) as Asset["kind"];
		const raw = form.get("file");
		// jsdom cannot send a File over XHR, so tests send "file:<name>" as a plain field
		const asText = typeof raw === "string";
		const file = asText
			? {
					name: raw.replace(/^file:/, ""),
					size: 4096,
					type: raw.endsWith(".mp4") ? "video/mp4" : raw.endsWith(".webm") ? "video/webm" : "image/png",
				}
			: (raw as File);
		const asset: Asset = {
			id: `00000000-0000-4000-8000-${String(landingState.assets.length + 1).padStart(12, "0")}`,
			kind,
			content_type: file.type || (kind === "video" ? "video/mp4" : "image/png"),
			bytes: file.size,
			width: kind === "video" ? null : 1200,
			height: kind === "video" ? null : 800,
			created_at: "2026-10-10T09:00:00Z",
			url: `/_landing/media/${kind}-${landingState.assets.length + 1}`,
		};
		landingState.assets.push(asset);
		landingState.calls.push({ method: "POST", path: "/api/v1/admin/landing/assets", body: { kind, name: file.name } });
		return HttpResponse.json(asset);
	}),
	http.delete(`${BASE}/api/v1/admin/landing/assets/:id`, ({ params }) => {
		const id = String(params.id);
		if (!landingState.assets.some((a) => a.id === id)) return coded(404, "landing_asset_not_found");
		if (referenced(id)) return coded(409, "landing_asset_in_use");
		landingState.assets = landingState.assets.filter((a) => a.id !== id);
		return new HttpResponse(null, { status: 204 });
	}),
	http.get(`${BASE}/api/v1/admin/landing/presets`, () => HttpResponse.json(PRESETS)),
	http.get(`${BASE}/api/v1/admin/landing/presets/:key`, ({ params, request }) => {
		const preset = PRESETS.find((p) => p.key === params.key);
		if (!preset) return coded(404, "landing_preset_not_found");
		const lang = new URL(request.url).searchParams.get("lang") ?? "en";
		const text = (value: string) => ({ en: value, ...(lang === "en" ? {} : { [lang]: value }) });
		return HttpResponse.json({
			conversation: { ...SAMPLE_CONVERSATION, preset: preset.key },
			sections: {
				pain_wall: {
					questions: [{ text: text("Is the clinic open on Sunday?"), channel: "whatsapp", time: "23:40" }],
					punchline: text("Your customers ask at night. Your phone is off."),
				},
				faq: { items: [{ q: text("Does it replace my staff?"), a: text("No. It answers the repeat questions.") }] },
			},
		});
	}),
	http.post(`${BASE}/api/v1/admin/landing/generate`, async ({ request }) => {
		await record(request);
		if (landingState.generateFails) {
			return coded(landingState.generateFails.status, landingState.generateFails.code);
		}
		return HttpResponse.json({
			conversation: { ...SAMPLE_CONVERSATION, preset: null, generated: true },
			sources: [{ title: "Timings", heading: "Saturday" }],
			cost_note: "This used one answer from your own AI model.",
		});
	}),
	http.post(`${BASE}/api/v1/admin/landing/assistant`, async ({ request }) => {
		await record(request);
		const body = (await request.json()) as { corpus_id: string };
		if (landingState.address == null) return coded(409, "landing_needs_address");
		landingState.askCorpus = body.corpus_id;
		landingState.askKey = "00000000-0000-4000-8000-0000000000aa";
		landingState.askStatus = "ready";
		return HttpResponse.json(stateOut());
	}),
	http.delete(`${BASE}/api/v1/admin/landing/assistant`, async ({ request }) => {
		await record(request);
		landingState.askCorpus = null;
		landingState.askKey = null;
		landingState.askStatus = "not_connected";
		return HttpResponse.json(stateOut());
	}),
];

/** For tests: how many texts the default draft has (a sanity check for the fixture). */
export const defaultTextCount = () => allTexts(DEFAULT).length;
