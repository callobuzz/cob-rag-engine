import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { BASE } from "@/test/msw/data";
import { server } from "@/test/msw/server";
import { ImproveRuleDialog } from "./ImproveRuleDialog";

const KB = "aaaaaaaa-0000-4000-8000-000000000001";
const RULE = { id: "results", type: "compliance", instruction: "tell ppl results r fine if normal" };
const URL = `${BASE}/api/v1/corpora/${KB}/assistant/rules/improve`;
const IMPROVED = "If someone asks about results, say a doctor will explain them and offer a callback.";

function renderDialog(rule = RULE) {
	const onAccept = vi.fn();
	const onClose = vi.fn();
	const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
	render(
		<QueryClientProvider client={qc}>
			<ImproveRuleDialog kb={KB} rule={rule} onAccept={onAccept} onClose={onClose} />
		</QueryClientProvider>,
	);
	return { onAccept, onClose, user: userEvent.setup() };
}

describe("ImproveRuleDialog", () => {
	it("shows the suggestion and conflicts, and hands the wording back only on a click", async () => {
		const bodies: unknown[] = [];
		server.use(
			http.post(URL, async ({ request }) => {
				bodies.push(await request.json());
				return HttpResponse.json({
					improved: IMPROVED,
					conflicts: [{ rule_id: "no-diagnosis", reason: "Both cover results." }],
					notes: "One objective.",
				});
			}),
		);
		const { onAccept, user } = renderDialog();
		expect(await screen.findByText(IMPROVED)).toBeInTheDocument();
		expect(screen.getByText("tell ppl results r fine if normal")).toBeInTheDocument();
		expect(screen.getByText(/no-diagnosis/)).toBeInTheDocument();
		expect(screen.getByText("One objective.")).toBeInTheDocument();
		expect(onAccept).not.toHaveBeenCalled();
		await user.click(screen.getByRole("button", { name: "Use this wording" }));
		expect(onAccept).toHaveBeenCalledWith(IMPROVED);
		expect(bodies).toEqual([{ instruction: RULE.instruction, type: "compliance", rule_id: "results" }]);
	});

	it("asks once, even when the dialog re-renders", async () => {
		let calls = 0;
		server.use(
			http.post(URL, () => {
				calls += 1;
				return HttpResponse.json({ improved: IMPROVED, conflicts: [], notes: "" });
			}),
		);
		renderDialog();
		await screen.findByText(IMPROVED);
		expect(calls).toBe(1);
	});

	it("says so when the rule is already clear and offers nothing to apply", async () => {
		server.use(http.post(URL, () => HttpResponse.json({ improved: RULE.instruction, conflicts: [], notes: "" })));
		renderDialog();
		expect(await screen.findByText("Your wording is already clear. Nothing to change.")).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Use this wording" })).toBeDisabled();
	});

	it("explains a missing model instead of failing silently", async () => {
		server.use(
			http.post(URL, () =>
				HttpResponse.json({ detail: "No AI model", code: "assistant_llm_unavailable" }, { status: 503 }),
			),
		);
		renderDialog();
		expect(await screen.findByText(/Set a model for this knowledge base first/)).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Keep my wording" })).toBeEnabled();
		expect(screen.getByRole("button", { name: "Use this wording" })).toBeDisabled();
	});

	it("shows the engine's words when the model did not answer", async () => {
		server.use(
			http.post(URL, () =>
				HttpResponse.json(
					{ detail: "The AI model did not answer. Try again.", code: "assistant_llm_failed" },
					{ status: 502 },
				),
			),
		);
		renderDialog();
		expect(await screen.findByRole("alert")).toHaveTextContent("The AI model did not answer. Try again.");
	});

	it("closes with Keep my wording", async () => {
		const { onClose, onAccept, user } = renderDialog();
		await user.click(await screen.findByRole("button", { name: "Keep my wording" }));
		expect(onClose).toHaveBeenCalled();
		expect(onAccept).not.toHaveBeenCalled();
	});
});
