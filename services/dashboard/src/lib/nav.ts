import {
	BadgeCheck,
	BookOpen,
	Bot,
	Boxes,
	ChartColumn,
	Contact,
	Cpu,
	CreditCard,
	Database,
	FileText,
	Gauge,
	GitBranch,
	Globe,
	House,
	Inbox,
	Info,
	Layers,
	LifeBuoy,
	Lightbulb,
	type LucideIcon,
	Mail,
	MessageCircle,
	MessageSquare,
	MessagesSquare,
	Palette,
	PanelsTopLeft,
	Receipt,
	Scale,
	ScrollText,
	Upload,
	User,
	Users,
	Wallet,
	Zap,
} from "lucide-react";
import type { Principal } from "@/lib/auth/principal";
import type { DashboardFeatureView, NavGate } from "@/lib/features"; // BIZ3
import { billingNavStrings } from "@/lib/strings/billing"; // BIL2
import { landingStrings } from "@/lib/strings/landing"; // LP4 landing
import { legalStrings } from "@/lib/strings/legal"; // P3 legal
import { pipelinesStrings } from "@/lib/strings/pipelines"; // PIP4
import { priceSheetStrings } from "@/lib/strings/price-sheet"; // TV3

/**
 * Every signed-in page: sidebar item, palette entry and the placeholder text shown until the
 * epic that builds it lands (mockup "soon" texts where the mockup has them).
 */
export type View = "admin" | "tenant";

export interface PageMeta {
	id: string;
	path: string;
	label: string;
	icon: LucideIcon;
	epic: string;
	/** Which views see it; omitted = both. */
	views?: View[];
	/** Only when GET /info says features.content_portal (or, for WhatsApp, features.whatsapp.available). */
	feature?: "content_portal" | "whatsapp";
	group: "" | "Content" | "Answer" | "Measure" | "Customers" | "Workspace" | "Engine" | "Hidden";
	summary: string;
	items: string[];
	// BIZ3
	/**
	 * Where the business (tenant) dashboard puts it: the plain top menu or the collapsed Advanced
	 * group, in `order`, under `label` when it differs, and the plan gate(s) that hide it.
	 */
	tenant?: { group: "" | "Advanced"; order: number; label?: string; gate?: NavGate | NavGate[] };
	// /BIZ3
}

export const PAGES: PageMeta[] = [
	{
		id: "overview",
		path: "/",
		label: "Overview",
		icon: House,
		epic: "D2",
		group: "",
		summary: "How your assistants are doing today: questions, confidence, spend and what needs attention.",
		items: [
			"Questions per day by source",
			"Setup checklist",
			"Knowledge gaps and recent jobs",
			"Service health (admin)",
		],
		tenant: { group: "", order: 1, label: "Home" },
	},
	// BIZ3
	{
		id: "knowledge",
		path: "/knowledge",
		label: "Knowledge",
		icon: BookOpen,
		epic: "BIZ3",
		views: ["tenant"],
		group: "",
		tenant: { group: "", order: 3 },
		summary: "What your assistant knows: pages from your website, articles, questions and answers, and files.",
		items: ["Pages learned from your website", "Write an article or a question and answer", "Upload files"],
	},
	{
		id: "assistant",
		path: "/assistant",
		label: "Assistant",
		icon: Bot,
		epic: "BIZ3",
		views: ["tenant"],
		group: "",
		tenant: { group: "", order: 4 },
		summary: "Your assistant's name, picture, tone, languages and what it does when it is not sure.",
		items: ["Name and business details", "Tone and languages", "When your assistant is not sure"],
	},
	// /BIZ3
	// TV6 whatsapp: shown only when the engine has it and the plan includes it
	{
		id: "whatsapp",
		path: "/whatsapp",
		label: "WhatsApp",
		icon: MessageCircle,
		epic: "TV6",
		views: ["tenant"],
		feature: "whatsapp",
		group: "",
		tenant: { group: "", order: 4.5 },
		summary: "Connect your WhatsApp number and let your assistant answer customers there, with you able to step in.",
		items: ["Connect your number", "Reply to a customer yourself", "Files your assistant can send"],
	},
	// /TV6 whatsapp
	{
		id: "kbs",
		path: "/kbs",
		label: "Knowledge bases",
		icon: Database,
		epic: "D3",
		group: "",
		summary: "Every knowledge base with its own documents, keys, model and search index.",
		items: ["Create a knowledge base", "Model & keys, prompt, retrieval presets", "Rotate its API key, move or delete"],
		tenant: { group: "Advanced", order: 1 },
	},
	{
		id: "docs",
		path: "/docs",
		label: "Docs",
		icon: BookOpen,
		epic: "COB8",
		group: "",
		summary: "Guides, help for every screen and the API reference, with search.",
		items: [
			"Search and a contents list on every page",
			"Copyable examples with your engine address",
			"Ask COB about the page",
		],
		tenant: { group: "", order: 7, label: "Help" },
	},
	{
		id: "documents",
		path: "/documents",
		label: "Documents",
		icon: FileText,
		epic: "D4",
		group: "Content",
		summary: "The documents in this knowledge base, their versions and the review queue.",
		items: ["Search, versions and restore", "Near-duplicate review", "Delete and merge (owners)"],
		tenant: { group: "Advanced", order: 2 },
	},
	{
		id: "add",
		path: "/add",
		label: "Add content",
		icon: Upload,
		epic: "D4",
		group: "Content",
		summary:
			"Drag-and-drop files with live progress through each stage, and records import from JSON or CSV with a field-mapping step.",
		items: [
			"Per-file stages: queued → parsing → chunking → embedding → done",
			"Near-duplicate review cards",
			"Records import with mapping and preview",
		],
		tenant: { group: "Advanced", order: 3 },
	},
	{
		id: "crawler",
		path: "/crawler",
		label: "Crawler",
		icon: Globe,
		epic: "D8",
		group: "Content",
		summary: "Crawl a customer's website, review pages, then deliver them into the knowledge base.",
		items: [
			"Seeds and sitemap, review mode on by default",
			"Per-page status and delivery errors",
			"Only public addresses are crawled",
		],
		tenant: { group: "Advanced", order: 4 },
	},
	{
		id: "playground",
		path: "/playground",
		label: "Playground",
		icon: MessageSquare,
		epic: "D5",
		group: "Answer",
		summary: "Ask questions and see the answer stream with its sources, scores and stage timings.",
		items: ["Streaming answers with sources", "Try settings and compare", "Feedback on answers"],
		tenant: { group: "Advanced", order: 5 },
	},
	{
		id: "widget",
		path: "/widget",
		label: "Website widget",
		icon: Boxes,
		epic: "D6",
		group: "Answer",
		summary: "Publishable keys for each site, with limits, branding and a live preview.",
		items: [
			"Allowed sites, per-visitor and daily limits",
			"Title, greeting, colour and position with live preview",
			"Copy-ready embed snippet",
		],
		tenant: { group: "Advanced", order: 6 },
	},
	{
		id: "agents",
		path: "/agents",
		label: "Agents (MCP)",
		icon: Bot,
		epic: "D6",
		group: "Answer",
		summary: "Connect this knowledge base to Claude, ChatGPT, Cursor or n8n with cob-rag-mcp.",
		items: [
			"Copyable configs with your engine URL",
			"Which tools each key kind sees",
			"You host it: no hosted service",
		],
		tenant: { group: "Advanced", order: 7, gate: ["keys_agents", "api_docs"] },
	},
	{
		id: "leads",
		path: "/leads",
		label: "Leads",
		icon: Contact,
		epic: "C4",
		group: "Answer",
		summary: "Contact details visitors left with their consent, with the chat they came from.",
		items: ["Filter by status, date, page and phone", "Status, assignee and notes", "CSV export and deletion (owners)"],
		tenant: { group: "", order: 2, label: "Conversations & Leads" },
	},
	{
		id: "usage",
		path: "/usage",
		label: "Usage",
		icon: ChartColumn,
		epic: "D2",
		group: "Measure",
		summary: "Questions, tokens and estimated cost by day, knowledge base and source, ready for invoicing.",
		items: ["Charts by source", "Filters by date, customer and knowledge base", "CSV export · ₹ / $ display"],
		tenant: { group: "Advanced", order: 8, gate: "usage_details" },
	},
	{
		id: "costs",
		path: "/admin/costs",
		label: "Costs",
		icon: Wallet,
		epic: "D2",
		views: ["admin"],
		group: "Measure",
		summary:
			"What the platform spends on models: real cost per request, by customer, knowledge base, model and source.",
		items: ["Today, this month and any range", "Breakdowns and CSV export", "OpenRouter balance"],
	},
	{
		id: "insights",
		path: "/insights",
		label: "Insights",
		icon: Lightbulb,
		epic: "D9",
		group: "Measure",
		summary: "What people ask and how well it's answered.",
		items: ["Query history with sources", "Confidence distribution", "Knowledge gaps and feedback"],
		tenant: { group: "Advanced", order: 9 },
	},
	{
		id: "review",
		path: "/review",
		label: "Review",
		icon: Inbox,
		epic: "C7",
		group: "Measure",
		summary: "Questions your assistant could not answer well, with one-click fixes.",
		items: [
			"Questions it could not answer, grouped",
			"Thumbs down and requests for a person",
			"Add an exact answer, a document or a rule",
		],
		tenant: { group: "Advanced", order: 10 },
	},
	{
		id: "quality",
		path: "/quality",
		label: "Quality",
		icon: BadgeCheck,
		epic: "D9",
		group: "Measure",
		summary: "Golden question sets and evaluation runs over time.",
		items: ["Golden sets per knowledge base", "Evaluation runs and score drops", "Optimisation suggestions"],
		tenant: { group: "Advanced", order: 11, gate: "quality" },
	},
	{
		id: "customers",
		path: "/admin/customers",
		label: "Customers",
		icon: Users,
		epic: "D7",
		views: ["admin"],
		group: "Customers",
		summary: "Every customer workspace: pending sign-ups, active and suspended customers.",
		items: [
			"Activate with a plan, decline",
			"Suspend, reactivate, change plan, delete",
			"Add a customer and invite its owner",
		],
	},
	{
		id: "plans",
		path: "/admin/plans",
		label: "Plans",
		icon: CreditCard,
		epic: "D7",
		views: ["admin"],
		group: "Customers",
		summary: "Plans with their limits: knowledge bases, questions a month, documents and widget keys.",
		items: ["Create and edit plans", "Default plan for open sign-ups", "Customers on each plan"],
	},
	// BIL2
	{
		id: "billing",
		path: "/admin/billing",
		label: billingNavStrings.label,
		icon: Wallet,
		epic: "BIL2",
		views: ["admin"],
		group: "Customers",
		summary: billingNavStrings.summary,
		items: [...billingNavStrings.items],
	},
	// /BIL2
	// TV3 support and price list
	{
		id: "support",
		path: "/admin/support",
		label: "Support",
		icon: LifeBuoy,
		epic: "TV3",
		views: ["admin"],
		group: "Customers",
		summary: "Requests your businesses write to you, with their reply thread and how fast you answer.",
		items: ["Filter by status and business", "Reply and set the status", "First reply time and your promise"],
	},
	{
		id: "price-sheet",
		path: "/admin/price-sheet",
		label: priceSheetStrings.nav.label,
		icon: Receipt,
		epic: "TV3",
		views: ["admin"],
		group: "Customers",
		summary: priceSheetStrings.nav.summary,
		items: [...priceSheetStrings.nav.items],
	},
	// /TV3 support and price list
	// P3 legal
	{
		id: "legal",
		path: "/admin/legal",
		label: legalStrings.nav.label,
		icon: Scale,
		epic: "P3",
		views: ["admin"],
		group: "Customers",
		summary: legalStrings.nav.summary,
		items: [...legalStrings.nav.items],
	},
	// /P3 legal
	// LP4 landing
	{
		id: "landing",
		path: "/admin/landing",
		label: landingStrings.nav.label,
		icon: PanelsTopLeft,
		epic: "LP4",
		views: ["admin"],
		group: "Customers",
		summary: landingStrings.nav.summary,
		items: [...landingStrings.nav.items],
	},
	// /LP4 landing
	{
		id: "branding",
		path: "/admin/branding",
		label: "Sign-up & branding",
		icon: Palette,
		epic: "D10",
		views: ["admin"],
		group: "Customers",
		summary: "Your product name, logo, colour and support contact, and how new customers sign up.",
		items: ["Open, approval or closed sign-up", "Logo, colour and support email with preview", "Contact text and link"],
	},
	{
		id: "email",
		path: "/admin/email",
		label: "Email (SMTP)",
		icon: Mail,
		epic: "D10",
		views: ["admin"],
		group: "Customers",
		summary: "Whether e-mail is set up, and a test send.",
		items: ["SMTP status", "Send a test email", "Which emails the engine sends"],
	},
	{
		id: "audit",
		path: "/admin/audit",
		label: "Audit log",
		icon: ScrollText,
		epic: "D10",
		views: ["admin"],
		group: "Customers",
		summary: "Who did what: activations, suspensions, role changes and sign-ins.",
		items: ["Filter by customer and action", "Actor and time for each change", "Links from customer history"],
	},
	{
		id: "team",
		path: "/team",
		label: "Team",
		icon: Users,
		epic: "D7",
		views: ["tenant"],
		group: "Workspace",
		summary: "The people in this workspace and their roles.",
		items: ["Invite teammates as Owner or Member", "Change roles and remove people", "Open invitations"],
		tenant: { group: "", order: 6 },
	},
	{
		id: "plan",
		path: "/plan",
		label: "Plan & billing", // BIL6
		icon: CreditCard,
		epic: "D2",
		views: ["tenant"],
		group: "Workspace",
		summary: "Your plan, balance and statements, and how much of each limit this month has used.",
		items: [
			"Balance or credit, usage this month and add-ons",
			"Monthly statements to view, download and print",
			"Request a top-up, or ask how to change plan",
		],
		tenant: { group: "", order: 5 },
	},
	{
		id: "migrations",
		path: "/migrations",
		label: "Migrations",
		icon: GitBranch,
		epic: "D10",
		group: "Engine",
		summary: "Embedding-model changes, built beside the live index.",
		items: ["Approve, watch progress", "Verify and switch", "Roll back"],
		tenant: { group: "Advanced", order: 13, gate: "retrieval" },
	},
	{
		id: "webhooks",
		path: "/admin/webhooks",
		label: "Webhooks",
		icon: Zap,
		epic: "D10",
		views: ["admin"],
		group: "Engine",
		summary: "Alerts sent to your systems.",
		items: ["Knowledge gap detected", "Evaluation score drop", "Settings changed"],
	},
	// PIP4
	{
		id: "pipelines",
		path: "/admin/pipelines",
		label: pipelinesStrings.nav.label,
		icon: Gauge,
		epic: "PIP4",
		views: ["admin"],
		group: "Engine",
		summary: pipelinesStrings.nav.summary,
		items: [...pipelinesStrings.nav.items],
	},
	// /PIP4
	{
		id: "models",
		path: "/models",
		label: "Models",
		icon: Cpu,
		epic: "D10",
		group: "Engine",
		summary: "The model catalog and retrieval presets.",
		items: ["Answer and embedding catalogs", "OpenRouter discovery", "Presets"],
		tenant: { group: "Advanced", order: 12, gate: "models" },
	},
	{
		id: "benchmarks",
		path: "/benchmarks",
		label: "Benchmarks",
		icon: Gauge,
		epic: "D10",
		group: "Engine",
		summary: "How the fast models scored on accuracy, speed and cost.",
		items: ["Fast-model results and quality bars", "Other measured results", "Method"],
		tenant: { group: "Advanced", order: 14, gate: "models" },
	},
	{
		id: "engine",
		path: "/admin/engine",
		label: "Engine info",
		icon: Info,
		epic: "D10",
		views: ["admin"],
		group: "Engine",
		summary: "Version, features, models and engine-wide retrieval defaults.",
		items: ["Version and enabled features", "Embedding and answer models", "Retrieval defaults"],
	},
	{
		id: "cob",
		path: "/admin/cob",
		label: "COB",
		icon: Bot,
		epic: "COB9",
		views: ["admin"],
		group: "Engine",
		summary: "The pages COB reads: the engine's own, and the ones you add.",
		items: ["Built-in docs and their sync", "Add your own pages", "Choose who COB answers them to"],
	},
	{
		id: "portal",
		path: "/portal",
		label: "Content Portal",
		icon: Layers,
		epic: "D11",
		feature: "content_portal",
		group: "Engine",
		summary: "AI clean-up of documents before they go live (when enabled).",
		items: ["Workspaces", "Chunk editor", "Review and publish"],
		tenant: { group: "Advanced", order: 15 },
	},
	{
		id: "conversations",
		path: "/conversations",
		label: "Conversations",
		icon: MessagesSquare,
		epic: "D5",
		group: "Hidden",
		summary: "Multi-turn conversations and their memory.",
		items: ["Conversation history", "Messages with sources", "Delete a conversation"],
	},
	{
		id: "profile",
		path: "/profile",
		label: "Profile & sessions",
		icon: User,
		epic: "D7",
		group: "Hidden",
		summary: "Your name, password and the devices you're signed in on.",
		items: ["Change name and password", "Signed-in sessions with revoke", "Log out everywhere else"],
	},
];

export const NAV_GROUPS: PageMeta["group"][] = ["", "Content", "Answer", "Measure", "Customers", "Workspace", "Engine"];

export function pageById(id: string): PageMeta {
	const p = PAGES.find((x) => x.id === id);
	if (!p) throw new Error(`unknown page ${id}`);
	return p;
}

// BIZ3
function gatesOf(p: PageMeta): NavGate[] {
	const g = p.tenant?.gate;
	return g === undefined ? [] : Array.isArray(g) ? g : [g];
}

/**
 * The pages a person can open. `dashboard` (the plan's flags, from `useDashboardFeatures`) narrows a
 * business's menu: a page behind a hidden gate, or the whole Advanced group, is not listed.
 */
export function visiblePages(
	principal: Principal | null,
	features: { content_portal?: boolean; whatsapp?: boolean } = {},
	dashboard?: DashboardFeatureView,
): PageMeta[] {
	if (!principal) return [];
	return PAGES.filter(
		(p) =>
			(!p.views || p.views.includes(principal.view)) &&
			(!p.feature || features[p.feature] === true) &&
			(principal.view !== "tenant" ||
				!dashboard ||
				((p.tenant?.group !== "Advanced" || !dashboard.advancedHidden) && gatesOf(p).every(dashboard.shown))),
	);
}

/** The business menu: the plain items, then what goes under Advanced, each in its own order. */
export function tenantNav(pages: readonly PageMeta[]): { top: PageMeta[]; advanced: PageMeta[] } {
	const placed = pages.filter((p) => p.tenant);
	const byOrder = (a: PageMeta, b: PageMeta) => (a.tenant?.order ?? 0) - (b.tenant?.order ?? 0);
	return {
		top: placed.filter((p) => p.tenant?.group === "").sort(byOrder),
		advanced: placed.filter((p) => p.tenant?.group === "Advanced").sort(byOrder),
	};
}

/** A page's menu name for this view (a business sees "Home" where the operator sees "Overview"). */
export function pageLabel(p: PageMeta, view: View): string {
	return view === "tenant" ? (p.tenant?.label ?? p.label) : p.label;
}
// /BIZ3
