import { describe, expect, it } from "vitest";
import { principalFromSession } from "@/lib/auth/principal";
import { type DashboardFeatures, featureView } from "@/lib/features";
import { NAV_GROUPS, pageLabel, tenantNav, visiblePages } from "@/lib/nav";

const OWNER = principalFromSession({
	user: { id: "u1", email: "priya@shree-labs.in", name: "Priya Kulkarni", is_superadmin: false },
	tenant: { id: "t1", name: "Shree Dental Clinic", role: "owner", status: "active", is_operator: false },
	tenants: [],
});
const ADMIN = principalFromSession({
	user: { id: "u0", email: "sv@sahayak.example", name: "SV", is_superadmin: true },
	tenant: null,
	tenants: [],
});

const ALL_ON: DashboardFeatures = {
	advanced: true,
	models: true,
	retrieval: true,
	keys_agents: true,
	webhooks: true,
	quality: true,
	api_docs: true,
	usage_details: true,
	widget_code_only: false,
};

function menu(features: Partial<DashboardFeatures> | null, content_portal = false) {
	const view = featureView(features ? { ...ALL_ON, ...features } : null, true);
	const pages = visiblePages(OWNER, { content_portal }, view);
	const { top, advanced } = tenantNav(pages);
	return {
		top: top.map((p) => pageLabel(p, "tenant")),
		advanced: advanced.map((p) => pageLabel(p, "tenant")),
	};
}

describe("business menu", () => {
	it("six plain things, Team and Help, in this order, in the business's words", () => {
		expect(menu(null).top).toEqual([
			"Home",
			"Conversations & Leads",
			"Knowledge",
			"Assistant",
			"Plan & billing",
			"Team",
			"Help",
		]);
	});

	it("everything technical sits under Advanced, in this order", () => {
		expect(menu(null).advanced).toEqual([
			"Knowledge bases",
			"Documents",
			"Add content",
			"Crawler",
			"Playground",
			"Website widget",
			"Agents (MCP)",
			"Usage",
			"Insights",
			"Review",
			"Quality",
			"Models",
			"Migrations",
			"Benchmarks",
		]);
	});

	it("Content Portal joins Advanced only when the engine has it", () => {
		expect(menu(null).advanced).not.toContain("Content Portal");
		expect(menu(null, true).advanced.at(-1)).toBe("Content Portal");
	});

	it("advanced off: no Advanced group at all, the plain menu stays", () => {
		const m = menu({ advanced: false });
		expect(m.advanced).toEqual([]);
		expect(m.top).toHaveLength(7);
	});

	it.each([
		["models", ["Models", "Benchmarks"]],
		["retrieval", ["Migrations"]],
		["keys_agents", ["Agents (MCP)"]],
		["api_docs", ["Agents (MCP)"]],
		["usage_details", ["Usage"]],
		["quality", ["Quality"]],
	] as const)("%s off hides %j", (flag, hidden) => {
		const m = menu({ [flag]: false });
		for (const label of hidden) expect(m.advanced, label).not.toContain(label);
		expect(m.advanced.length).toBe(14 - hidden.length);
	});

	it("webhooks off changes nothing in the menu (it has no page of its own for a business)", () => {
		expect(menu({ webhooks: false }).advanced).toHaveLength(14);
	});

	it("code-only widget keeps the widget page listed", () => {
		expect(menu({ widget_code_only: true }).advanced).toContain("Website widget");
	});

	it("never lists a page that belongs to the operator", () => {
		const all = [...menu(null).top, ...menu(null).advanced];
		for (const label of ["Customers", "Plans", "Costs", "Audit log", "Engine info", "COB", "Overview"]) {
			expect(all, label).not.toContain(label);
		}
	});

	it("pageLabel: the operator's Overview is a business's Home", () => {
		const overview = visiblePages(OWNER).find((p) => p.id === "overview");
		const operator = visiblePages(ADMIN).find((p) => p.id === "overview");
		expect(overview && pageLabel(overview, "tenant")).toBe("Home");
		expect(operator && pageLabel(operator, "admin")).toBe("Overview");
	});
});

describe("operator menu is unchanged", () => {
	it("the same pages in the same groups as before the business menu", () => {
		const groups = Object.fromEntries(visiblePages(ADMIN).map((p) => [p.id, p.group]));
		expect(groups).toEqual({
			overview: "",
			kbs: "",
			docs: "",
			documents: "Content",
			add: "Content",
			crawler: "Content",
			playground: "Answer",
			widget: "Answer",
			agents: "Answer",
			leads: "Answer",
			usage: "Measure",
			costs: "Measure",
			insights: "Measure",
			review: "Measure",
			quality: "Measure",
			customers: "Customers",
			plans: "Customers",
			billing: "Customers",
			support: "Customers", // TV3
			"price-sheet": "Customers", // TV3
			landing: "Customers", // LP4 landing
			legal: "Customers", // P3 legal
			branding: "Customers",
			email: "Customers",
			audit: "Customers",
			migrations: "Engine",
			webhooks: "Engine",
			pipelines: "Engine", // PIP4
			models: "Engine",
			benchmarks: "Engine",
			engine: "Engine",
			cob: "Engine",
			conversations: "Hidden",
			profile: "Hidden",
		});
		expect(NAV_GROUPS).toEqual(["", "Content", "Answer", "Measure", "Customers", "Workspace", "Engine"]);
	});

	it("the plan's flags never narrow the operator's pages", () => {
		const view = featureView({ ...ALL_ON, advanced: false, models: false }, false);
		expect(visiblePages(ADMIN, {}, view).map((p) => p.id)).toEqual(visiblePages(ADMIN).map((p) => p.id));
	});
});

describe("feature view", () => {
	it("nothing restricted: everything is shown", () => {
		const v = featureView(null, true);
		for (const g of ["models", "retrieval", "keys_agents", "webhooks", "quality", "widget_settings"] as const) {
			expect(v.shown(g), g).toBe(true);
		}
		expect(v.codeOnlyWidget).toBe(false);
		expect(v.advancedHidden).toBe(false);
	});

	it("advanced off hides every technical area but not the widget's own form", () => {
		const v = featureView({ ...ALL_ON, advanced: false }, true);
		expect(v.advancedHidden).toBe(true);
		for (const g of [
			"models",
			"retrieval",
			"keys_agents",
			"webhooks",
			"quality",
			"api_docs",
			"usage_details",
		] as const) {
			expect(v.shown(g), g).toBe(false);
		}
		expect(v.shown("widget_settings")).toBe(true);
	});

	it("code-only is a restriction, not a switch that is shown", () => {
		const v = featureView({ ...ALL_ON, widget_code_only: true }, true);
		expect(v.codeOnlyWidget).toBe(true);
		expect(v.shown("widget_settings")).toBe(false);
	});
});

describe("WhatsApp (TV6)", () => {
	const top = (features: { content_portal?: boolean; whatsapp?: boolean }, who = OWNER) =>
		tenantNav(visiblePages(who, features, featureView(null, true))).top.map((p) => pageLabel(p, "tenant"));

	it("is a plain menu item after Assistant, only when the engine and the plan have it", () => {
		expect(top({})).not.toContain("WhatsApp");
		expect(top({ whatsapp: false })).not.toContain("WhatsApp");
		const withIt = top({ whatsapp: true });
		expect(withIt.indexOf("WhatsApp")).toBe(withIt.indexOf("Assistant") + 1);
	});

	it("is never in the operator's menu", () => {
		expect(visiblePages(ADMIN, { whatsapp: true }).map((p) => p.id)).not.toContain("whatsapp");
	});
});
