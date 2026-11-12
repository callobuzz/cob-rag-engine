import { HttpResponse, http } from "msw";
import { BASE } from "../data";
import { coded } from "./auth";

/**
 * P3 handlers: the legal pages editor (drafts, preview, publish), the legal settings, who accepted
 * what, and the public list sign-up reads. Stateful within a test (reset in setup.ts), with the
 * engine's rules: a draft is saved against its revision (409 `legal_draft_conflict`), publishing
 * needs `confirm_reviewed` (422 `legal_review_required`) and a value for every placeholder the text
 * uses (422 `legal_placeholders_missing`). Indian demo data: Sahayak AI, Pune.
 */
export const SLUGS = [
	"terms",
	"privacy",
	"refunds",
	"delivery",
	"acceptable-use",
	"dpa",
	"sub-processors",
	"grievance",
] as const;

const TITLES: Record<string, string> = {
	terms: "Terms of Service",
	privacy: "Privacy Notice",
	refunds: "Refund and Cancellation Policy",
	delivery: "Shipping and Delivery Policy",
	"acceptable-use": "Acceptable Use Policy",
	dpa: "Data Processing Addendum",
	"sub-processors": "Sub-processors",
	grievance: "Grievance Officer",
};

interface Version {
	version: number;
	published_at: string;
	published_by: string | null;
	change_note: string | null;
	/** The draft revision this version was published at (the engine copies it). */
	rev?: number;
}
interface Draft {
	title: string;
	markdown: string;
	revision: number;
}
interface Settings {
	grievance_name: string | null;
	grievance_email: string | null;
	grievance_address: string | null;
	jurisdiction_city: string | null;
	website: string | null;
	show_in_footer: boolean;
}

const TEMPLATE_MARK = "[//]: # (TEMPLATE — have your lawyer review this page before you publish it.)";
export const template = (slug: string) =>
	`${TEMPLATE_MARK}\n\n# ${TITLES[slug]}\n\nWrite to {{grievance_email}} at {{product_name}}.\n`;

const blankSettings = (): Settings => ({
	grievance_name: null,
	grievance_email: null,
	grievance_address: null,
	jurisdiction_city: null,
	website: null,
	show_in_footer: true,
});

export const legalState = {
	drafts: new Map<string, Draft>(),
	published: new Map<string, Version[]>(),
	settings: blankSettings(),
	acceptances: [] as {
		id: string;
		accepted_at: string;
		email: string;
		tenant_id: string | null;
		workspace: string | null;
		page: string;
		version: number;
		via: string;
	}[],
	sent: [] as { method: string; path: string; body: unknown }[],
};

export function resetLegalState() {
	legalState.drafts.clear();
	legalState.published.clear();
	legalState.settings = blankSettings();
	legalState.acceptances = [];
	legalState.sent = [];
}

/** Publish `slug` directly (a starting point for a test). */
export function seedPublished(slug: string, version = 1, when = "2026-10-08T09:00:00Z") {
	const list = legalState.published.get(slug) ?? [];
	list.push({ version, published_at: when, published_by: "sv@punedigital.in", change_note: null });
	legalState.published.set(slug, list);
}

const latest = (slug: string) => legalState.published.get(slug)?.at(-1) ?? null;

/** Placeholders in `markdown` that have no value yet (the engine's rule, for the two the template uses). */
function missingFor(markdown: string): string[] {
	const missing: string[] = [];
	if (markdown.includes("{{grievance_email}}") && !legalState.settings.grievance_email) missing.push("grievance_email");
	return missing;
}

function detail(slug: string) {
	const draft = legalState.drafts.get(slug);
	const markdown = draft?.markdown ?? template(slug);
	return {
		slug,
		title: draft?.title ?? TITLES[slug],
		markdown,
		revision: draft?.revision ?? 0,
		is_default_template: draft == null,
		published: latest(slug),
		history: [...(legalState.published.get(slug) ?? [])].reverse(),
		missing: missingFor(markdown),
		company_changed: false,
		review_note: "This is a starting template. Have your lawyer review it before you publish it.",
	};
}

function summary(slug: string) {
	const draft = legalState.drafts.get(slug);
	const live = latest(slug);
	return {
		slug,
		title: TITLES[slug],
		published_version: live?.version ?? null,
		published_at: live?.published_at ?? null,
		has_draft: draft != null,
		draft_changed: draft != null && (live == null || draft.revision !== live.rev),
		draft_revision: draft?.revision ?? 0,
		default_available: true,
		company_changed: false,
		review_note: "This is a starting template. Have your lawyer review it before you publish it.",
	};
}

const known = (slug: unknown): slug is string =>
	typeof slug === "string" && (SLUGS as readonly string[]).includes(slug);

export const legalHandlers = [
	// the public list: sign-up reads it to know whether to ask for acceptance
	http.get(`${BASE}/api/v1/public/legal`, () =>
		HttpResponse.json({
			items: SLUGS.flatMap((slug) => {
				const live = latest(slug);
				return live
					? [
							{
								slug,
								title: TITLES[slug],
								version: live.version,
								published_at: live.published_at,
								url: `https://api.sahayak.example/legal/${slug}`,
							},
						]
					: [];
			}),
		}),
	),
	http.get(`${BASE}/api/v1/admin/legal`, () => HttpResponse.json({ items: SLUGS.map(summary) })),
	http.get(`${BASE}/api/v1/admin/legal/settings`, () => HttpResponse.json(legalState.settings)),
	http.put(`${BASE}/api/v1/admin/legal/settings`, async ({ request }) => {
		const body = (await request.json()) as Settings;
		legalState.sent.push({ method: "PUT", path: "settings", body });
		if (body.grievance_email && !body.grievance_email.includes("@")) return coded(422, "validation_error");
		legalState.settings = { ...blankSettings(), ...body };
		return HttpResponse.json(legalState.settings);
	}),
	http.get(`${BASE}/api/v1/admin/legal/acceptances`, ({ request }) => {
		const url = new URL(request.url);
		if (url.searchParams.get("format") === "csv") {
			const lines = legalState.acceptances.map(
				(a) => `${a.accepted_at},${a.email},${a.workspace ?? ""},${a.page},${a.version}`,
			);
			return new HttpResponse(["accepted_at,email,workspace,page,version", ...lines].join("\n"), {
				headers: { "content-type": "text/csv; charset=utf-8" },
			});
		}
		const offset = Number(url.searchParams.get("offset") ?? 0);
		const limit = Number(url.searchParams.get("limit") ?? 50);
		return HttpResponse.json({
			total: legalState.acceptances.length,
			items: legalState.acceptances.slice(offset, offset + limit),
		});
	}),
	http.get(`${BASE}/api/v1/admin/legal/:slug`, ({ params }) =>
		known(params.slug) ? HttpResponse.json(detail(params.slug)) : coded(404, "legal_page_not_found"),
	),
	http.put(`${BASE}/api/v1/admin/legal/:slug/draft`, async ({ params, request }) => {
		if (!known(params.slug)) return coded(404, "legal_page_not_found");
		const body = (await request.json()) as { title: string; markdown: string; base_revision: number };
		legalState.sent.push({ method: "PUT", path: `${params.slug}/draft`, body });
		const current = legalState.drafts.get(params.slug);
		if ((current?.revision ?? 0) !== body.base_revision) return coded(409, "legal_draft_conflict");
		legalState.drafts.set(params.slug, {
			title: body.title,
			markdown: body.markdown,
			revision: (current?.revision ?? 0) + 1,
		});
		return HttpResponse.json(detail(params.slug));
	}),
	http.post(`${BASE}/api/v1/admin/legal/:slug/preview`, async ({ params, request }) => {
		if (!known(params.slug)) return coded(404, "legal_page_not_found");
		const body = (await request.json()) as { markdown: string };
		const missing = missingFor(body.markdown);
		const html = body.markdown
			.replace(/\{\{grievance_email\}\}/g, legalState.settings.grievance_email ?? "[[grievance_email]]")
			.replace(/\{\{product_name\}\}/g, "Sahayak AI");
		return HttpResponse.json({ html: `<p>${html.replace(/[<>]/g, "")}</p>`, missing });
	}),
	http.post(`${BASE}/api/v1/admin/legal/:slug/publish`, async ({ params, request }) => {
		if (!known(params.slug)) return coded(404, "legal_page_not_found");
		const body = (await request.json()) as { change_note?: string | null; confirm_reviewed?: boolean };
		legalState.sent.push({ method: "POST", path: `${params.slug}/publish`, body });
		if (!body.confirm_reviewed) return coded(422, "legal_review_required");
		const draft = legalState.drafts.get(params.slug);
		const missing = missingFor(draft?.markdown ?? template(params.slug));
		if (missing.length) return coded(422, "legal_placeholders_missing", { missing });
		if (!draft)
			legalState.drafts.set(params.slug, {
				title: TITLES[params.slug] ?? "",
				markdown: template(params.slug),
				revision: 1,
			});
		const list = legalState.published.get(params.slug) ?? [];
		const version = (list.at(-1)?.version ?? 0) + 1;
		const row = {
			version,
			published_at: "2026-10-08T10:00:00Z",
			published_by: "sv@punedigital.in",
			change_note: body.change_note ?? null,
			rev: legalState.drafts.get(params.slug)?.revision,
		};
		legalState.published.set(params.slug, [...list, row]);
		return HttpResponse.json(detail(params.slug));
	}),
];
