import type { World } from "./mock-engine";

/**
 * The Docs API (COB8) for the fake engine: a small tree per view (the operator also gets the
 * settings reference), pages with a contents list and a curl example, and search by title and
 * summary. Same shapes as src/test/msw/handlers/docs.ts. Stateless.
 */
interface Page {
	slug: string;
	title: string;
	topic: string;
	route: string | null;
	summary: string;
	markdown: string;
	toc: { anchor: string; title: string; level: number }[];
	adminOnly?: boolean;
}

const VERSION = "0.1.0+abc123def456";

const PAGES: Page[] = [
	{
		slug: "screens/widget",
		title: "Website widget",
		topic: "widget",
		route: "/widget",
		summary: "Keys, allowed sites, look and the embed snippet for your website.",
		markdown:
			"Put the chat on your website.\n\n## Embed the widget\n\nPaste the snippet before the closing body tag.\n\n## Allowed sites\n\nList the sites that may show the widget.",
		toc: [
			{ anchor: "embed-the-widget", title: "Embed the widget", level: 2 },
			{ anchor: "allowed-sites", title: "Allowed sites", level: 2 },
		],
	},
	{
		slug: "api/assistant",
		title: "Assistant API",
		topic: "api",
		route: null,
		summary: "Every assistant route of the engine API, with a curl example.",
		markdown:
			'## List the assistant\n\n```bash\ncurl {{BASE_URL}}/api/v1/assistant -H "X-API-Key: $COB_RAG_KEY"\n```\n\n## Update\n\nPatch it.',
		toc: [
			{ anchor: "list-the-assistant", title: "List the assistant", level: 2 },
			{ anchor: "update", title: "Update", level: 2 },
		],
	},
	{
		slug: "changelog/unreleased",
		title: "Unreleased",
		topic: "changelog",
		route: null,
		summary: "The Docs section.",
		markdown: "Added the Docs section.",
		toc: [],
	},
	{
		slug: "settings/llm-tuning",
		title: "LLM tuning settings",
		topic: "settings",
		route: null,
		summary: "Every RAG_ setting for the model.",
		markdown: "## RAG_LLM_MODEL\n\nThe model.",
		toc: [{ anchor: "rag_llm_model", title: "RAG_LLM_MODEL", level: 2 }],
		adminOnly: true,
	},
];

const TOPICS = [
	{ id: "widget", title: "Website widget" },
	{ id: "api", title: "API reference" },
	{ id: "settings", title: "Settings reference" },
	{ id: "changelog", title: "Changelog" },
];

export interface DocsCtx {
	/** The operator view (the super admin outside a workspace). */
	isAdmin: boolean;
	reply: (status: number, payload?: unknown) => unknown;
}

export async function handleDocsRoute(_w: World, method: string, url: URL, ctx: DocsCtx): Promise<boolean> {
	const path = url.pathname;
	if (method !== "GET" || !path.startsWith("/api/v1/docs/")) return false;
	const pages = PAGES.filter((p) => ctx.isAdmin || !p.adminOnly);

	if (path === "/api/v1/docs/tree") {
		await ctx.reply(200, {
			version: VERSION,
			topics: TOPICS.map((t) => ({
				...t,
				pages: pages
					.filter((p) => p.topic === t.id)
					.map((p) => ({ slug: p.slug, title: p.title, route: p.route, summary: p.summary })),
			})).filter((t) => t.pages.length > 0),
		});
		return true;
	}
	if (path === "/api/v1/docs/search") {
		const q = (url.searchParams.get("q") ?? "").toLowerCase();
		await ctx.reply(200, {
			items: pages
				.filter((p) => `${p.title} ${p.summary}`.toLowerCase().includes(q))
				.map((p) => ({
					slug: p.slug,
					anchor: p.toc[0]?.anchor ?? "",
					title: p.toc[0] ? `${p.title} — ${p.toc[0].title}` : p.title,
					snippet: p.summary,
					route: p.route,
					score: 0.9,
				})),
		});
		return true;
	}
	if (path.startsWith("/api/v1/docs/pages/")) {
		const slug = decodeURIComponent(path.slice("/api/v1/docs/pages/".length));
		const page = pages.find((p) => p.slug === slug);
		if (!page) {
			await ctx.reply(404, { detail: "That help page does not exist.", code: "docs_page_not_found" });
			return true;
		}
		const same = pages.filter((p) => p.topic === page.topic);
		const at = same.indexOf(page);
		const nb = (p: Page | undefined) => (p ? { slug: p.slug, title: p.title } : null);
		const { adminOnly: _a, ...rest } = page;
		await ctx.reply(200, {
			...rest,
			version: VERSION,
			public_url: null,
			prev: nb(same[at - 1]),
			next: nb(same[at + 1]),
		});
		return true;
	}
	return false;
}
