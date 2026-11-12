import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, rt, SHREE, SV, tokenResponse } from "@/test/msw/data";
import { FULL_INFO } from "@/test/msw/handlers/engine";
import { createEvalsMock } from "@/test/msw/handlers/evals";
import { createQualityMock } from "@/test/msw/handlers/quality";
import { server } from "@/test/msw/server";
import { renderApp } from "@/test/render";

const KB = CORPORA[0]?.id ?? "";

/** Sign in as the knowledge base's owner or the operator; `/info` says whether RAGAS is installed. */
function open(path: string, opts: { ragas: boolean; who?: "owner" | "admin"; criteria?: boolean }) {
	const admin = opts.who === "admin";
	const user = admin ? SV : PRIYA;
	const tenant = admin ? null : SHREE;
	const tenants = tenant ? [tenant] : [];
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () => HttpResponse.json(tokenResponse({ user, tenant, tenants }, 2))),
		http.get(`${BASE}/api/v1/auth/me`, () => HttpResponse.json({ user, tenant, tenants, session_id: "s" })),
		http.get(`${BASE}/api/v1/info`, () =>
			HttpResponse.json({ ...FULL_INFO, features: { ...FULL_INFO.features, eval_ragas: opts.ragas } }),
		),
	);
	if (tenant) {
		server.use(
			http.get(`${BASE}/api/v1/corpora`, () => {
				const items = CORPORA.filter((c) => c.tenant_id === SHREE.id);
				return HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 500 });
			}),
		);
	}
	server.use(...createQualityMock().handlers, ...createEvalsMock().handlers);
	if (opts.criteria === false) {
		server.use(http.get(`${BASE}/api/v1/corpora/:kb/eval/criteria`, () => HttpResponse.json({ items: [] })));
	}
	return renderApp(path);
}

describe("quality: the evaluation add-on", () => {
	it("add-on missing: a friendly card, and the features that still work stay usable", async () => {
		const { user } = open(`/quality?kb=${KB}&tab=runs`, { ragas: false });
		const card = await screen.findByTestId("eval-addon-missing");
		expect(screen.getByRole("region", { name: "Evaluation add-on not installed" })).toBeInTheDocument();
		expect(card).toHaveTextContent("pip install 'cob-rag-engine[eval]'");
		expect(card).toHaveTextContent("RAG_INSTALL_EVAL=true");
		expect(card).toHaveTextContent("Everything else works");
		// golden sets, quick checks and the tabs are all still there
		expect(screen.getByRole("tab", { name: "Golden sets" })).toBeEnabled();
		await user.click(await screen.findByRole("button", { name: "New run" }));
		const dlg = await screen.findByRole("dialog", { name: "New run" });
		expect(within(dlg).getByRole("radio", { name: /quick check/i })).toBeEnabled();
		expect(within(dlg).getByRole("button", { name: "Start run" })).toBeEnabled();
	});

	it("add-on missing and no criteria: a full run is disabled with the reason", async () => {
		const { user } = open(`/quality?kb=${KB}&tab=runs`, { ragas: false, criteria: false });
		await user.click(await screen.findByRole("button", { name: "New run" }));
		const dlg = await screen.findByRole("dialog", { name: "New run" });
		await waitFor(() => expect(within(dlg).getByRole("radio", { name: /full run/i })).toBeDisabled());
		expect(within(dlg).getByRole("note")).toHaveTextContent("Add one on the Criteria tab");
		expect(within(dlg).getByRole("radio", { name: /quick check/i })).toBeEnabled();
	});

	it("add-on missing but a criterion exists: a full run is allowed and says what it scores", async () => {
		const { user } = open(`/quality?kb=${KB}&tab=runs`, { ragas: false });
		await user.click(await screen.findByRole("button", { name: "New run" }));
		const dlg = await screen.findByRole("dialog", { name: "New run" });
		expect(await within(dlg).findByRole("note")).toHaveTextContent("scores your own criteria");
		expect(within(dlg).getByRole("radio", { name: /full run/i })).toBeEnabled();
	});

	it("add-on missing: the operator's traffic evaluation cannot be started", async () => {
		open(`/quality?kb=${KB}&tab=runs`, { ragas: false, who: "admin" });
		const button = await screen.findByRole("button", { name: "Run evaluation" });
		expect(button).toBeDisabled();
		expect(button).toHaveAttribute("title", "Needs the evaluation add-on");
	});

	it("add-on installed: no card, a full run is open, the operator can trigger", async () => {
		const { user } = open(`/quality?kb=${KB}&tab=runs`, { ragas: true, who: "admin" });
		expect(await screen.findByRole("button", { name: "Run evaluation" })).toBeEnabled();
		expect(screen.queryByTestId("eval-addon-missing")).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "New run" }));
		const dlg = await screen.findByRole("dialog", { name: "New run" });
		expect(within(dlg).getByRole("radio", { name: /full run/i })).toBeEnabled();
		expect(within(dlg).queryByRole("note")).not.toBeInTheDocument();
	});
});

describe("quality: the add-on and optimisation", () => {
	it("add-on missing: the operator cannot start an optimisation run", async () => {
		const { user } = open(`/quality?kb=${KB}&tab=optimize`, { ragas: false, who: "admin" });
		const button = await screen.findByRole("button", { name: "Run optimisation" });
		expect(button).toBeDisabled();
		expect(button).toHaveAttribute("title", "Needs the evaluation add-on");
		expect(screen.getByTestId("eval-addon-missing")).toHaveTextContent("optimisation runs cannot start");
		await user.click(screen.getByRole("tab", { name: "Golden sets" }));
		expect(await screen.findByRole("tab", { name: "Golden sets", selected: true })).toBeInTheDocument();
	});
});
