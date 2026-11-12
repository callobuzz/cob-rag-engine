/**
 * Legal pages part of the fake engine (P3). Follows `api/routes/legal_admin.py` and
 * `legal_public.py`: eight pages with a shipped template, a draft saved against its revision,
 * publishing needs `confirm_reviewed` and a value for every placeholder the text uses, and the
 * published pages show in the public branding's footer links and the public list sign-up reads.
 * Indian demo data: Sahayak AI, Pune.
 */
const SLUGS = ["terms", "privacy", "refunds", "delivery", "acceptable-use", "dpa", "sub-processors", "grievance"];
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
const ENGINE = "https://api.sahayak.example";
const NOTE = "This is a starting template. Have your lawyer review it before you publish it.";

interface Version {
	version: number;
	published_at: string;
	published_by: string | null;
	change_note: string | null;
	rev: number;
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

export interface LegalMock {
	drafts: Record<string, Draft>;
	published: Record<string, Version[]>;
	settings: Settings;
	sent: { method: string; path: string; body: unknown }[];
}

export const createLegalState = (): LegalMock => ({
	drafts: {},
	published: {},
	settings: {
		grievance_name: null,
		grievance_email: null,
		grievance_address: null,
		jurisdiction_city: null,
		website: null,
		show_in_footer: true,
	},
	sent: [],
});

export interface LegalCtx {
	isAdmin: boolean;
	/** The public branding served to every page: its `legal` footer links follow the published pages. */
	branding: { legal?: { slug: string; title: string; url: string }[] };
	reply: (status: number, body?: unknown) => Promise<void>;
}

const template = (slug: string) =>
	`[//]: # (TEMPLATE — have your lawyer review this page before you publish it.)\n\n# ${TITLES[slug]}\n\nWrite to {{grievance_email}} at {{product_name}}.\n`;
const latest = (s: LegalMock, slug: string) => s.published[slug]?.at(-1) ?? null;
const missingFor = (s: LegalMock, markdown: string) =>
	markdown.includes("{{grievance_email}}") && !s.settings.grievance_email ? ["grievance_email"] : [];

function detail(s: LegalMock, slug: string) {
	const draft = s.drafts[slug];
	const markdown = draft?.markdown ?? template(slug);
	return {
		slug,
		title: draft?.title ?? TITLES[slug],
		markdown,
		revision: draft?.revision ?? 0,
		is_default_template: !draft,
		published: latest(s, slug),
		history: [...(s.published[slug] ?? [])].reverse(),
		missing: missingFor(s, markdown),
		company_changed: false,
		review_note: NOTE,
	};
}

function summary(s: LegalMock, slug: string) {
	const draft = s.drafts[slug];
	const live = latest(s, slug);
	return {
		slug,
		title: TITLES[slug],
		published_version: live?.version ?? null,
		published_at: live?.published_at ?? null,
		has_draft: !!draft,
		draft_changed: !!draft && (!live || draft.revision !== live.rev),
		draft_revision: draft?.revision ?? 0,
		default_available: true,
		company_changed: false,
		review_note: NOTE,
	};
}

function syncFooter(s: LegalMock, ctx: LegalCtx) {
	ctx.branding.legal = s.settings.show_in_footer
		? SLUGS.flatMap((slug) =>
				latest(s, slug) ? [{ slug, title: TITLES[slug] as string, url: `${ENGINE}/legal/${slug}` }] : [],
			)
		: [];
}

export async function handleLegalRoute(
	s: LegalMock,
	method: string,
	path: string,
	body: Record<string, unknown>,
	ctx: LegalCtx,
): Promise<boolean> {
	if (method === "GET" && path === "/api/v1/public/legal") {
		const items = SLUGS.flatMap((slug) => {
			const live = latest(s, slug);
			return live
				? [
						{
							slug,
							title: TITLES[slug],
							version: live.version,
							published_at: live.published_at,
							url: `${ENGINE}/legal/${slug}`,
						},
					]
				: [];
		});
		await ctx.reply(200, { items });
		return true;
	}
	if (!path.startsWith("/api/v1/admin/legal")) return false;
	if (!ctx.isAdmin) {
		await ctx.reply(403, { detail: "Admin access required" });
		return true;
	}
	if (method === "GET" && path === "/api/v1/admin/legal") {
		await ctx.reply(200, { items: SLUGS.map((slug) => summary(s, slug)) });
		return true;
	}
	if (path === "/api/v1/admin/legal/settings") {
		if (method === "PUT") {
			s.sent.push({ method, path, body });
			s.settings = { ...s.settings, ...(body as unknown as Settings) };
			syncFooter(s, ctx);
		}
		await ctx.reply(200, s.settings);
		return true;
	}
	if (method === "GET" && path === "/api/v1/admin/legal/acceptances") {
		await ctx.reply(200, { items: [], total: 0 });
		return true;
	}
	const m = /^\/api\/v1\/admin\/legal\/([^/]+)(?:\/(draft|preview|publish))?$/.exec(path);
	const slug = m?.[1];
	if (!m || !slug || !SLUGS.includes(slug)) {
		await ctx.reply(404, { detail: "That legal page does not exist.", code: "legal_page_not_found" });
		return true;
	}
	const action = m[2];
	if (method === "GET" && !action) {
		await ctx.reply(200, detail(s, slug));
		return true;
	}
	if (method === "PUT" && action === "draft") {
		s.sent.push({ method, path, body });
		const current = s.drafts[slug];
		if ((current?.revision ?? 0) !== body.base_revision) {
			await ctx.reply(409, { detail: "This page was changed by someone else.", code: "legal_draft_conflict" });
			return true;
		}
		s.drafts[slug] = {
			title: String(body.title),
			markdown: String(body.markdown),
			revision: (current?.revision ?? 0) + 1,
		};
		await ctx.reply(200, detail(s, slug));
		return true;
	}
	if (method === "POST" && action === "preview") {
		const markdown = String(body.markdown ?? "");
		const html = markdown
			.replace(/\{\{grievance_email\}\}/g, s.settings.grievance_email ?? "[[grievance_email]]")
			.replace(/\{\{product_name\}\}/g, "Sahayak AI")
			.replace(/[<>]/g, "");
		await ctx.reply(200, { html: `<p>${html}</p>`, missing: missingFor(s, markdown) });
		return true;
	}
	if (method === "POST" && action === "publish") {
		s.sent.push({ method, path, body });
		if (!body.confirm_reviewed) {
			await ctx.reply(422, { detail: "Confirm the review.", code: "legal_review_required" });
			return true;
		}
		const draft = s.drafts[slug];
		const missing = missingFor(s, draft?.markdown ?? template(slug));
		if (missing.length) {
			await ctx.reply(422, { detail: "Missing.", code: "legal_placeholders_missing", missing });
			return true;
		}
		s.drafts[slug] ??= { title: TITLES[slug] as string, markdown: template(slug), revision: 1 };
		const list = s.published[slug] ?? [];
		s.published[slug] = [
			...list,
			{
				version: (list.at(-1)?.version ?? 0) + 1,
				published_at: "2026-10-08T10:00:00Z",
				published_by: "sv@punedigital.in",
				change_note: typeof body.change_note === "string" ? body.change_note : null,
				rev: s.drafts[slug].revision,
			},
		];
		syncFooter(s, ctx);
		await ctx.reply(200, detail(s, slug));
		return true;
	}
	return false;
}
