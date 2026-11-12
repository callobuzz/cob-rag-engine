import { HttpResponse, http } from "msw";
import { BASE } from "../data";

/**
 * The Docs API (COB8): a small tree per role, pages with a contents list and a curl example, and
 * a search that matches titles and summaries. `docsState.admin` shows the administrator pages,
 * `docsState.searchDown` makes search answer 503 `docs_search_unavailable`. `resetDocsState()`.
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

export const DOCS_VERSION = "0.1.0+abc123def456";

export const DOCS_PAGES: Page[] = [
	{
		slug: "screens/widget",
		title: "Website widget",
		topic: "widget",
		route: "/widget",
		summary: "Keys, allowed sites, look and the embed snippet for your website.",
		markdown:
			"Put the chat on your website.\n\n## Embed the widget\n\nPaste the snippet before the closing body tag. See the [assistant API](/docs/api/assistant).\n\n## Allowed sites\n\nList the sites that may show the widget.",
		toc: [
			{ anchor: "embed-the-widget", title: "Embed the widget", level: 2 },
			{ anchor: "allowed-sites", title: "Allowed sites", level: 2 },
		],
	},
	{
		slug: "screens/kb-detail",
		title: "Knowledge base",
		topic: "knowledge-bases",
		route: "/kbs/$kbId",
		summary: "One knowledge base: its model, keys and settings.",
		markdown: "Everything about one knowledge base.\n\n## Settings\n\nChange the model.",
		toc: [{ anchor: "settings", title: "Settings", level: 2 }],
	},
	{
		slug: "screens/docs",
		title: "Docs",
		topic: "screens",
		route: "/docs/$",
		summary: "Search the guides and open the help for any screen.",
		markdown: "The Docs hold every guide.\n\n## Search\n\nPress / to search.",
		toc: [{ anchor: "search", title: "Search", level: 2 }],
	},
	{
		slug: "api/assistant",
		title: "assistant",
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
		slug: "changelog/intro",
		title: "Changelog",
		topic: "changelog",
		route: null,
		summary: "All notable changes to the engine.",
		markdown: "All notable changes.",
		toc: [],
	},
	{
		slug: "guide/flow",
		title: "How a customer flows",
		topic: "api",
		route: null,
		summary: "From sign-up to the first answer.",
		markdown: "Start here.\n\n## Sign-up modes\n\nOpen or by approval.\n\n## Create a customer\n\nAs the operator.",
		toc: [
			{ anchor: "signup-modes", title: "Sign-up modes", level: 2 },
			{ anchor: "create-customer", title: "Create a customer", level: 2 },
		],
	},
	{
		slug: "changelog/unreleased",
		title: "Unreleased",
		topic: "changelog",
		route: null,
		summary: "COB8: the Docs section.",
		markdown: "Added the Docs section.",
		toc: [],
	},
	{
		slug: "settings/llm-tuning",
		title: "Settings reference",
		topic: "settings",
		route: null,
		summary: "Every RAG_ setting.",
		markdown: "## RAG_LLM_MODEL\n\nThe model.",
		toc: [{ anchor: "rag_llm_model", title: "RAG_LLM_MODEL", level: 2 }],
		adminOnly: true,
	},
];

const TOPICS = [
	{ id: "knowledge-bases", title: "Knowledge bases & uploads" },
	{ id: "widget", title: "Website widget" },
	{ id: "api", title: "API reference" },
	{ id: "settings", title: "Settings reference" },
	{ id: "changelog", title: "Changelog" },
	{ id: "screens", title: "Screens" },
];

export const docsState = { admin: false, searchDown: false };

export function resetDocsState(): void {
	docsState.admin = false;
	docsState.searchDown = false;
}

const visible = () => DOCS_PAGES.filter((p) => docsState.admin || !p.adminOnly);

export const docsHandlers = [
	http.get(`${BASE}/api/v1/docs/tree`, () =>
		HttpResponse.json({
			version: DOCS_VERSION,
			topics: TOPICS.map((t) => ({
				...t,
				pages: visible()
					.filter((p) => p.topic === t.id)
					.map((p) => ({ slug: p.slug, title: p.title, route: p.route, summary: p.summary })),
			})).filter((t) => t.pages.length > 0),
		}),
	),
	http.get(`${BASE}/api/v1/docs/pages/*`, ({ request }) => {
		const slug = decodeURIComponent(new URL(request.url).pathname.replace("/api/v1/docs/pages/", ""));
		const pages = visible();
		const page = pages.find((p) => p.slug === slug);
		if (!page) {
			return HttpResponse.json(
				{ detail: "That help page does not exist.", code: "docs_page_not_found" },
				{ status: 404 },
			);
		}
		const same = pages.filter((p) => p.topic === page.topic);
		const at = same.indexOf(page);
		const nb = (p: Page | undefined) => (p ? { slug: p.slug, title: p.title } : null);
		const { adminOnly: _a, ...rest } = page;
		return HttpResponse.json({
			...rest,
			version: DOCS_VERSION,
			public_url: null,
			prev: nb(same[at - 1]),
			next: nb(same[at + 1]),
		});
	}),
	http.get(`${BASE}/api/v1/docs/search`, ({ request }) => {
		if (docsState.searchDown) {
			return HttpResponse.json(
				{ detail: "Search is not available right now.", code: "docs_search_unavailable" },
				{ status: 503 },
			);
		}
		const q = (new URL(request.url).searchParams.get("q") ?? "").toLowerCase();
		const items = visible()
			.filter((p) => `${p.title} ${p.summary}`.toLowerCase().includes(q))
			.map((p) => ({
				slug: p.slug,
				anchor: p.toc[0]?.anchor ?? "",
				title: p.toc[0] ? `${p.title} — ${p.toc[0].title}` : p.title,
				snippet: p.summary,
				route: p.route,
				score: 0.9,
			}));
		return HttpResponse.json({ items });
	}),
];
