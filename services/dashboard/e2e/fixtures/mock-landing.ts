import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * The public website's editor on the fake engine (LP4). Follows `api/routes/landing_admin.py`: a
 * draft is saved against its revision (409 `landing_revision_conflict`), switching on needs a
 * published version and an address, publishing needs every switched-on language complete (409
 * `landing_translation_missing` with `paths`), a file in use cannot be deleted. The preview link
 * points back at this fake engine, which answers with a small page (no scripts of the real site).
 * Indian demo data: Sharma Dental Clinic, Pune.
 */
type Json = Record<string, unknown>;
interface Fixture {
	content: Json & { home: Json[]; languages: string[]; theme: Json };
	limits: Record<string, number>;
	theme: Json & { adjusted: string[]; light: Record<string, string>; css: string };
}

const load = (): Fixture =>
	JSON.parse(
		readFileSync(resolve(process.cwd(), "src/test/msw/handlers/landing-default.fixture.json"), "utf8"),
	) as Fixture;

interface Asset {
	id: string;
	kind: "logo" | "poster" | "video";
	content_type: string;
	bytes: number;
	width: number | null;
	height: number | null;
	created_at: string;
	url: string;
}

export interface LandingMock {
	fixture: Fixture;
	draft: Fixture["content"];
	revision: number;
	enabled: boolean;
	address: string | null;
	versions: {
		version: number;
		published_at: string;
		published_by: string;
		change_note: string | null;
		content: Json;
	}[];
	assets: Asset[];
	askCorpus: string | null;
	askKey: string | null;
	askStatus: string;
	calls: { method: string; path: string; body: unknown }[];
}

export const createLandingState = (): LandingMock => {
	const fixture = load();
	return {
		fixture,
		draft: structuredClone(fixture.content),
		revision: 1,
		enabled: false,
		address: null,
		versions: [],
		assets: [],
		askCorpus: null,
		askKey: null,
		askStatus: "not_connected",
		calls: [],
	};
};

export interface LandingCtx {
	isAdmin: boolean;
	raw: () => Buffer | null;
	reply: (status: number, body?: unknown) => Promise<void>;
	html: (text: string) => Promise<void>;
}

const PRESETS = [
	{ key: "clinic", name: "Clinic or hospital", languages: ["en", "hi", "hi-Latn"] },
	{ key: "shop", name: "Shop or D2C brand", languages: ["en", "hi", "hi-Latn"] },
	{ key: "coaching", name: "Coaching or school", languages: ["en", "hi", "hi-Latn"] },
	{ key: "realestate", name: "Real estate", languages: ["en", "hi", "hi-Latn"] },
	{ key: "salon", name: "Salon or spa", languages: ["en", "hi", "hi-Latn"] },
	{ key: "services", name: "Services", languages: ["en", "hi", "hi-Latn"] },
];

const CONVERSATION = {
	preset: "clinic",
	assistant_name: { en: "Asha" },
	business_name: { en: "Sharma Dental Clinic, Pune" },
	generated: false,
	turns: [
		{ role: "visitor", text: { en: "Is Dr. Sharma available on Saturday evening?" } },
		{
			role: "assistant",
			text: { en: "Yes, Saturday 5 to 8 pm. Shall I note your number so the clinic can confirm?" },
			source: { en: "Timings page · updated 2 Oct" },
		},
	],
	lead: null,
};

/** Every text of the enabled parts that lacks `lang` (the default language is never short). */
function gaps(draft: Json, lang: string): string[] {
	const out: string[] = [];
	const isText = (v: unknown): v is Record<string, string> =>
		!!v &&
		typeof v === "object" &&
		!Array.isArray(v) &&
		Object.keys(v).length > 0 &&
		Object.keys(v).every((k) => ["en", "hi", "hi-Latn"].includes(k));
	const walk = (v: unknown, path: string) => {
		if (isText(v)) {
			if (!v[lang]?.trim()) out.push(path);
		} else if (Array.isArray(v)) {
			v.forEach((x, i) => {
				walk(x, `${path}[${i}]`);
			});
		} else if (v && typeof v === "object") for (const [k, x] of Object.entries(v)) walk(x, path ? `${path}.${k}` : k);
	};
	for (const s of draft.home as Json[]) {
		if (s.enabled === false) continue;
		const { id, type: _t, ...rest } = s;
		walk(rest, `home.${String(id)}`);
	}
	for (const k of ["nav", "footer", "seo"]) walk(draft[k], k);
	for (const k of ["pricing", "features", "about", "contact"])
		if ((draft[k] as Json).enabled !== false) walk(draft[k], k);
	return out;
}

function stateOut(s: LandingMock) {
	const live = s.versions.at(-1);
	const warnings: { code: string; message: string }[] = [];
	if (s.address == null)
		warnings.push({
			code: "address_missing",
			message: "Set your website's address. Without it the site cannot be switched on.",
		});
	if (!live) warnings.push({ code: "nothing_published", message: "Nothing is published yet." });
	const theme = structuredClone(s.fixture.theme);
	const brand = String((s.draft.theme as Json).brand_color ?? "#0f766e");
	theme.light = { ...theme.light, brand };
	return {
		address: s.address,
		address_effective: s.address,
		ask: { corpus_id: s.askCorpus, widget_key_id: s.askKey, status: s.askStatus },
		assets: s.assets,
		copy_warnings: [],
		draft: s.draft,
		draft_revision: s.revision,
		draft_theme: theme,
		enabled: s.enabled,
		limits: { ...s.fixture.limits, "upload.video_mb": 25, "upload.image_mb": 5 },
		published: live
			? { version: live.version, published_at: live.published_at, published_by: live.published_by }
			: null,
		warnings,
	};
}

/** The page the preview frames show: a headline that reports its line count, in the draft's colour. */
function previewPage(s: LandingMock, dark: boolean, page: string): string {
	const brand = String((s.draft.theme as Json).brand_color ?? "#0f766e");
	const hero = s.draft.home[0] as Json;
	const headline = String((hero.headline as Record<string, string> | undefined)?.en ?? "");
	return `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Preview</title>
<style>body{margin:0;font:16px/1.5 system-ui,sans-serif;background:${dark ? "#101817" : "#f4fcfb"};color:${dark ? "#f4fcfb" : "#101817"}}
main{padding:32px 24px}h1{font-size:34px;line-height:1.15;margin:0 0 12px}a{display:inline-block;background:${brand};color:#fff;padding:10px 18px;border-radius:999px;text-decoration:none}</style>
<main><p style="opacity:.6;margin:0 0 8px">${page}</p><h1 id="h">${headline.replace(/</g, "&lt;")}</h1><p>Answers your customers' questions on your website, day and night.</p><a href="#">Start free</a></main>
<script>if(location.search.includes("preview=1")){var h=document.getElementById("h");var lh=parseFloat(getComputedStyle(h).lineHeight);parent.postMessage({type:"landing-preview",headlineLines:Math.round(h.getBoundingClientRect().height/lh)},"*")}</script>`;
}

export async function handleLandingRoute(
	s: LandingMock,
	method: string,
	path: string,
	body: Json,
	ctx: LandingCtx,
	search = "",
): Promise<boolean> {
	const m = /^\/_landing\/preview\/(\w+)$/.exec(path);
	if (method === "GET" && m) {
		await ctx.html(previewPage(s, search.includes("theme=dark"), m[1] ?? "home"));
		return true;
	}
	if (!path.startsWith("/api/v1/admin/landing")) return false;
	if (!ctx.isAdmin) {
		await ctx.reply(403, { detail: "Only the operator can do this.", code: "admin_required" });
		return true;
	}
	const sub = path.slice("/api/v1/admin/landing".length);
	const done = async (status: number, payload?: unknown) => {
		await ctx.reply(status, payload);
		return true;
	};
	const conflict = () =>
		done(409, { detail: "Someone saved this website in another tab.", code: "landing_revision_conflict" });
	s.calls.push({ method, path, body });

	if (method === "GET" && sub === "") return done(200, stateOut(s));
	if (method === "PUT" && sub === "/draft") {
		if (body.base_revision !== s.revision) return conflict();
		s.draft = body.content as Fixture["content"];
		s.revision += 1;
		return done(200, stateOut(s));
	}
	if (method === "PUT" && sub === "/settings") {
		if ("address" in body) s.address = body.address ? String(body.address).replace(/\/$/, "") : null;
		if (body.enabled === true) {
			if (s.versions.length === 0)
				return done(409, {
					detail: "Publish your website once before switching it on.",
					code: "landing_nothing_published",
				});
			if (s.address == null)
				return done(409, { detail: "Set your website's address first.", code: "landing_needs_address" });
		}
		if (typeof body.enabled === "boolean") s.enabled = body.enabled;
		return done(200, stateOut(s));
	}
	if (method === "POST" && sub === "/theme/preview") {
		const theme = structuredClone(s.fixture.theme);
		theme.light = { ...theme.light, brand: String(body.brand_color) };
		if (/^#[ef]/i.test(String(body.brand_color)))
			theme.adjusted = ["Buttons use a darker shade of your colour so their text stays readable.", ...theme.adjusted];
		return done(200, theme);
	}
	if (method === "POST" && sub === "/preview-link") {
		return done(200, {
			url: `http://engine.mock/_landing/preview/${String(body.page)}?t=token${s.revision}&lang=${String(body.lang)}`,
			expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
		});
	}
	if (method === "POST" && sub === "/publish") {
		if (body.base_revision !== s.revision) return conflict();
		for (const lang of s.draft.languages) {
			if (lang === "en") continue;
			const missing = gaps(s.draft, lang);
			if (missing.length > 0)
				return done(409, {
					detail: `Some texts are missing in ${lang}. Fill them in or switch that language off.`,
					code: "landing_translation_missing",
					paths: missing.slice(0, 50),
					language: lang,
				});
		}
		s.versions.push({
			version: s.versions.length + 1,
			published_at: "2026-10-10T09:30:00Z",
			published_by: "sv@sahayak.example",
			change_note: (body.change_note as string | null) ?? null,
			content: structuredClone(s.draft),
		});
		return done(200, stateOut(s));
	}
	if (method === "GET" && sub === "/versions") {
		const items = [...s.versions].reverse().map(({ content: _c, ...v }) => v);
		return done(200, { items });
	}
	const restore = /^\/versions\/(\d+)\/restore$/.exec(sub);
	if (method === "POST" && restore) {
		if (body.base_revision !== s.revision) return conflict();
		const found = s.versions.find((v) => v.version === Number(restore[1]));
		if (!found) return done(404, { detail: "That version does not exist.", code: "landing_version_not_found" });
		s.draft = structuredClone(found.content) as Fixture["content"];
		s.revision += 1;
		return done(200, stateOut(s));
	}
	if (method === "POST" && sub === "/assets") {
		const raw = ctx.raw()?.toString("latin1") ?? "";
		const kind = /name="kind"\r\n\r\n(\w+)/.exec(raw)?.[1] as Asset["kind"] | undefined;
		const name = /filename="([^"]+)"/.exec(raw)?.[1] ?? "file";
		if (!kind) return done(422, { detail: "kind is required", code: "invalid" });
		const asset: Asset = {
			id: `00000000-0000-4000-8000-${String(s.assets.length + 1).padStart(12, "0")}`,
			kind,
			content_type: kind === "video" ? "video/mp4" : "image/png",
			bytes: raw.length,
			width: kind === "video" ? null : 1200,
			height: kind === "video" ? null : 800,
			created_at: "2026-10-10T09:00:00Z",
			url: `/_landing/media/${kind}-${s.assets.length + 1}.${name.split(".").pop()}`,
		};
		s.assets.push(asset);
		return done(200, asset);
	}
	const asset = /^\/assets\/([\w-]+)$/.exec(sub);
	if (method === "DELETE" && asset) {
		if (JSON.stringify(s.draft).includes(asset[1] ?? "§")) {
			return done(409, {
				detail: "This file is used on your website. Remove it from the page first.",
				code: "landing_asset_in_use",
			});
		}
		s.assets = s.assets.filter((a) => a.id !== asset[1]);
		return done(204);
	}
	if (method === "GET" && sub === "/presets") return done(200, PRESETS);
	if (method === "GET" && sub.startsWith("/presets/")) {
		return done(200, {
			conversation: CONVERSATION,
			sections: {
				pain_wall: {
					questions: [
						{ text: { en: "Is the clinic open on Sunday?" }, channel: "whatsapp", time: "23:40" },
						{ text: { en: "Do you do root canals for children?" }, channel: "website", time: "22:15" },
					],
					punchline: { en: "Your patients ask at night. Your phone is off." },
				},
			},
		});
	}
	if (method === "POST" && sub === "/generate") {
		return done(200, {
			conversation: { ...CONVERSATION, preset: null, generated: true },
			sources: [{ title: "Timings", heading: "Saturday" }],
			cost_note: "This used one answer from your own AI model.",
		});
	}
	if (method === "POST" && sub === "/assistant") {
		if (s.address == null)
			return done(409, { detail: "Set your website's address first.", code: "landing_needs_address" });
		s.askCorpus = String(body.corpus_id);
		s.askKey = "00000000-0000-4000-8000-0000000000aa";
		s.askStatus = "ready";
		return done(200, stateOut(s));
	}
	if (method === "DELETE" && sub === "/assistant") {
		s.askCorpus = null;
		s.askKey = null;
		s.askStatus = "not_connected";
		return done(200, stateOut(s));
	}
	return false;
}
