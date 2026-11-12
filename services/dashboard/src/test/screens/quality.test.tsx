import { screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { BASE, CORPORA, PRIYA, ROHAN, rt, SHREE, SV, tokenResponse } from "../msw/data";
import type { Logged } from "../msw/handlers/insights";
import { createQualityMock, EVAL_DONE, OPT_ALL, OPT_DONE } from "../msw/handlers/quality";
import { server } from "../msw/server";
import { renderApp } from "../render";

// jsdom cannot send a FormData holding a File through fetch or XHR to MSW (D4 note): the upload
// helper is swapped for a fetch that sends the file text with its name in a header.
vi.mock("@/lib/api/upload", async () => {
	const { errorFromBody, toApiError } = await import("@/lib/api/errors");
	const { currentRagUrl } = await import("@/lib/auth/store");
	return {
		upload: async (opts: { path: string; buildForm: () => FormData }) => {
			const file = opts.buildForm().get("file");
			if (!(file instanceof Blob)) throw new Error("no file");
			let res: Response;
			try {
				res = await fetch(`${currentRagUrl()}${opts.path}`, {
					method: "POST",
					headers: { "content-type": "text/csv", "x-file-name": (file as File).name },
					body: await file.text(),
				});
			} catch (e) {
				throw toApiError(e);
			}
			const body: unknown = await res.json();
			if (res.ok) return body;
			throw errorFromBody(res.status, body, res.headers);
		},
	};
});

const PATIENT = CORPORA[0]?.id ?? "";
const MEMBER_SHREE = { ...SHREE, role: "member" as const };

type Who = "owner" | "member" | "admin";

function signIn(who: Who) {
	const user = who === "admin" ? SV : who === "member" ? ROHAN : PRIYA;
	const tenant = who === "admin" ? null : who === "member" ? MEMBER_SHREE : SHREE;
	const tenants = tenant ? [tenant] : [];
	sessionStorage.setItem(RT_KEY, rt(1));
	server.use(
		http.post(`${BASE}/api/v1/auth/refresh`, () => HttpResponse.json(tokenResponse({ user, tenant, tenants }, 2))),
		http.get(`${BASE}/api/v1/auth/me`, () => HttpResponse.json({ user, tenant, tenants, session_id: "s" })),
	);
	if (tenant) {
		server.use(
			http.get(`${BASE}/api/v1/corpora`, () => {
				const items = CORPORA.filter((c) => c.tenant_id === SHREE.id);
				return HttpResponse.json({ items, count: items.length, total: items.length, offset: 0, limit: 500 });
			}),
		);
	}
}

function open(path: string, who: Who = "owner") {
	signIn(who);
	const mock = createQualityMock({ memberSession: who === "member" });
	server.use(...mock.handlers);
	const app = renderApp(path);
	return { ...app, mock };
}

const calls = (log: Logged[], method: string, path: string) =>
	log.filter((l) => l.method === method && l.path === path);

/** Blobs handed to URL.createObjectURL (downloads). */
let downloads: { blob: Blob; name: string }[] = [];
beforeEach(() => {
	downloads = [];
	let last: Blob | null = null;
	URL.createObjectURL = vi.fn((b: Blob) => {
		last = b;
		return "blob:mock";
	});
	URL.revokeObjectURL = vi.fn();
	vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
		if (last) downloads.push({ blob: last, name: this.download });
	});
});
afterEach(() => vi.restoreAllMocks());

describe("quality: golden sets", () => {
	it("clears the expected-answer error as soon as a behaviour is picked", async () => {
		const { user } = open(`/quality?kb=${PATIENT}`);
		await user.click(await screen.findByRole("button", { name: "Add question" }));
		const add = await screen.findByRole("dialog", { name: "Add a golden question" });
		await user.click(within(add).getByRole("button", { name: "Add question" }));
		expect(await within(add).findByText("Add an expected answer or pick an expected behaviour.")).toBeInTheDocument();
		await user.selectOptions(within(add).getByLabelText("Expected behaviour"), "decline");
		await waitFor(() =>
			expect(within(add).queryByText("Add an expected answer or pick an expected behaviour.")).not.toBeInTheDocument(),
		);
	});

	it("lists, adds, edits and deletes golden questions", async () => {
		const { mock, user } = open(`/quality?kb=${PATIENT}`);
		expect(await screen.findByRole("heading", { level: 1, name: "Quality" })).toBeInTheDocument();
		const table = await screen.findByRole("table", { name: "Golden set · Patient help" });
		expect(within(table).getByText("Do I need to fast for a lipid profile?")).toBeInTheDocument();
		expect(within(table).getByText("Added by hand")).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Add question" }));
		const add = await screen.findByRole("dialog", { name: "Add a golden question" });
		await user.click(within(add).getByRole("button", { name: "Add question" }));
		expect(await within(add).findByText("Required")).toBeInTheDocument();
		expect(within(add).getByText("Add an expected answer or pick an expected behaviour.")).toBeInTheDocument();
		await user.type(within(add).getByLabelText("Question"), "थायरॉईड चाचणीसाठी उपाशी राहावे लागते का?");
		await user.type(within(add).getByLabelText("Expected answer"), "नाही, थायरॉईड चाचणीसाठी उपवास लागत नाही.");
		await user.type(within(add).getByLabelText("Expected passage ids (optional)"), "not-an-id");
		await user.click(within(add).getByRole("button", { name: "Add question" }));
		expect(await within(add).findByText("“not-an-id” isn't a passage id")).toBeInTheDocument();
		await user.clear(within(add).getByLabelText("Expected passage ids (optional)"));
		await user.type(
			within(add).getByLabelText("Expected passage ids (optional)"),
			"c4a11000-0000-4000-8000-000000000001, c4a11000-0000-4000-8000-000000000002",
		);
		await user.click(within(add).getByRole("button", { name: "Add question" }));
		await waitFor(() => expect(calls(mock.log, "POST", "/api/v1/golden")).toHaveLength(1));
		expect(calls(mock.log, "POST", "/api/v1/golden")[0]?.body).toEqual({
			corpus_id: PATIENT,
			origin: "human",
			query: "थायरॉईड चाचणीसाठी उपाशी राहावे लागते का?",
			expected_answer: "नाही, थायरॉईड चाचणीसाठी उपवास लागत नाही.",
			expected_chunk_ids: ["c4a11000-0000-4000-8000-000000000001", "c4a11000-0000-4000-8000-000000000002"],
		});
		expect(await screen.findByText("थायरॉईड चाचणीसाठी उपाशी राहावे लागते का?")).toBeInTheDocument();

		await user.click(
			screen.getByRole("button", { name: "Edit golden question: Do I need to fast for a lipid profile?" }),
		);
		const edit = await screen.findByRole("dialog", { name: "Edit golden question" });
		const answer = within(edit).getByLabelText("Expected answer");
		await user.clear(answer);
		await user.type(answer, "Yes, 10 to 12 hours.");
		await user.click(within(edit).getByRole("button", { name: "Save changes" }));
		await waitFor(() => expect(mock.log.filter((l) => l.method === "PATCH")).toHaveLength(1));
		expect(mock.log.find((l) => l.method === "PATCH")?.body).toEqual({
			query: "Do I need to fast for a lipid profile?",
			expected_answer: "Yes, 10 to 12 hours.",
			expected_chunk_ids: null,
		});

		await user.click(
			screen.getByRole("button", { name: "Delete golden question: Do I need to fast for a lipid profile?" }),
		);
		const del = await screen.findByRole("alertdialog", { name: "Delete this golden question?" });
		await user.click(within(del).getByRole("button", { name: "Delete" }));
		await waitFor(() => expect(mock.log.filter((l) => l.method === "DELETE")).toHaveLength(1));
		expect(await screen.findByText("Golden question deleted")).toBeInTheDocument();
	});

	it("adds a question that has only an expected behaviour", async () => {
		const { mock, user } = open(`/quality?kb=${PATIENT}`);
		await screen.findByRole("table", { name: "Golden set · Patient help" });
		await user.click(screen.getByRole("button", { name: "Add question" }));
		const add = await screen.findByRole("dialog", { name: "Add a golden question" });
		await user.type(within(add).getByLabelText("Question"), "Kya aap MRI karte ho?");
		await user.selectOptions(within(add).getByLabelText("Expected behaviour"), "no_answer");
		await user.type(within(add).getByLabelText("Must mention (optional)"), "pathology");
		await user.click(within(add).getByRole("button", { name: "Add question" }));
		await waitFor(() => expect(calls(mock.log, "POST", "/api/v1/golden")).toHaveLength(1));
		expect(calls(mock.log, "POST", "/api/v1/golden")[0]?.body).toEqual({
			corpus_id: PATIENT,
			origin: "human",
			query: "Kya aap MRI karte ho?",
			expected_chunk_ids: null,
			expected_behaviour: "no_answer",
			must_include: ["pathology"],
		});
		expect(await screen.findByText("Says it doesn't know")).toBeInTheDocument();
	});

	it("a member can add but deleting is locked (owner-only since A8)", async () => {
		const { mock, user } = open(`/quality?kb=${PATIENT}`, "member");
		await screen.findByRole("table", { name: "Golden set · Patient help" });
		const del = screen.getByRole("button", { name: "Delete golden question: Do I need to fast for a lipid profile?" });
		expect(del).toHaveAttribute("aria-disabled", "true");
		await user.click(del);
		expect(await screen.findAllByText("Only owners can delete golden questions")).not.toHaveLength(0);
		expect(screen.queryByRole("alertdialog")).toBeNull();
		expect(mock.log.filter((l) => l.method === "DELETE")).toHaveLength(0);
		expect(screen.getByRole("button", { name: "Add question" })).toBeEnabled();
	});

	it("imports a CSV of Hindi and English pairs after a preview; refuses a file without the columns", async () => {
		const { mock, user } = open(`/quality?kb=${PATIENT}`);
		await screen.findByRole("table", { name: "Golden set · Patient help" });
		await user.click(screen.getByRole("button", { name: "Import CSV" }));
		const dlg = await screen.findByRole("dialog", { name: "Import golden questions" });

		await user.upload(within(dlg).getByLabelText("CSV file"), new File(["question,answer\na,b\n"], "bad.csv"));
		expect(await within(dlg).findByText(/must name the columns query and expected_answer/)).toBeInTheDocument();
		// Import stays enabled but refuses a bad file, naming the problem.
		await user.click(within(dlg).getByRole("button", { name: "Import" }));
		expect(calls(mock.log, "POST", "/api/v1/golden/import")).toHaveLength(0);
		expect(within(dlg).getByLabelText("CSV file")).toHaveAttribute("aria-invalid", "true");

		const csv =
			"﻿query,expected_answer\r\nIs home collection free?,Free above ₹999\r\nरविवार को खुला है?,हाँ सुबह 7 से 11\r\nCan I get reports on WhatsApp?,Yes on +91 98220 12345\r\n";
		await user.upload(within(dlg).getByLabelText("CSV file"), new File([csv], "faq-golden.csv", { type: "text/csv" }));
		expect(await within(dlg).findByTestId("import-preview")).toHaveTextContent("3 rows ready · 0 will be skipped");
		await user.click(within(dlg).getByRole("button", { name: "Import" }));
		expect(await screen.findByText("Imported 3 golden questions")).toBeInTheDocument();
		expect(calls(mock.log, "POST", "/api/v1/golden/import")[0]?.query).toEqual({ corpus_id: PATIENT });
		// The byte-order mark is removed before upload (the engine reads plain UTF-8).
		expect(mock.uploads[0]?.startsWith("query,expected_answer")).toBe(true);
		expect(await screen.findByText("रविवार को खुला है?")).toBeInTheDocument();
	});

	it("downloads the template and exports JSON and CSV", async () => {
		const { mock, user } = open(`/quality?kb=${PATIENT}`);
		await screen.findByRole("table", { name: "Golden set · Patient help" });
		await user.click(screen.getByRole("button", { name: "Import CSV" }));
		await user.click(await screen.findByRole("button", { name: "Download a template" }));
		expect(downloads[0]?.name).toBe("golden-template.csv");
		expect(await downloads[0]?.blob.text()).toMatch(/^query,expected_answer,expected_chunk_ids\r\n/);
		await user.keyboard("{Escape}");

		await user.click(screen.getByRole("button", { name: "Export" }));
		await user.click(await screen.findByRole("menuitem", { name: "Download CSV" }));
		await waitFor(() => expect(downloads).toHaveLength(2));
		expect(downloads[1]?.name).toMatch(/^golden-patient-help-\d{4}-\d{2}-\d{2}\.csv$/);
		expect(await downloads[1]?.blob.text()).toContain("Do I need to fast for a lipid profile?,");
		await user.click(screen.getByRole("button", { name: "Export" }));
		await user.click(await screen.findByRole("menuitem", { name: "Download JSON" }));
		await waitFor(() => expect(downloads).toHaveLength(3));
		expect(JSON.parse(await (downloads[2]?.blob.text() ?? Promise.resolve("[]")))).toHaveLength(1);
		expect(calls(mock.log, "GET", "/api/v1/golden/export")).toHaveLength(2);
	});
});

describe("quality: evaluation and optimisation runs", () => {
	it("tenants see their own runs, an optimisation note, and no admin route is called", async () => {
		const { mock, user } = open(`/quality?kb=${PATIENT}&tab=runs`);
		expect(await screen.findByRole("table", { name: "Runs" })).toBeInTheDocument();
		expect(screen.queryByRole("table", { name: "Evaluation runs" })).not.toBeInTheDocument();
		await user.click(screen.getByRole("tab", { name: "Optimisation" }));
		expect(await screen.findByTestId("tenant-note")).toHaveTextContent("Optimisation is run by Sahayak AI");
		expect(mock.log.filter((l) => l.path.startsWith("/api/v1/admin/"))).toHaveLength(0);
	});

	it("the old tab=evals link opens Runs", async () => {
		open(`/quality?kb=${PATIENT}&tab=evals`);
		expect(await screen.findByRole("table", { name: "Runs" })).toBeInTheDocument();
	});

	it("admin: runs list, trigger → detail polling → scores in plain words", async () => {
		const { mock, user } = open(`/quality?kb=${PATIENT}&tab=runs`, "admin");
		const table = await screen.findByRole("table", { name: "Evaluation runs" });
		expect(within(table).getByText("Completed")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Run evaluation" }));
		const dlg = await screen.findByRole("dialog", { name: "Run an evaluation" });
		await user.type(within(dlg).getByLabelText("How many golden questions (optional)"), "0");
		await user.click(within(dlg).getByRole("button", { name: "Run evaluation" }));
		expect(await within(dlg).findByText("A whole number, 1 or more")).toBeInTheDocument();
		await user.clear(within(dlg).getByLabelText("How many golden questions (optional)"));
		await user.type(within(dlg).getByLabelText("How many golden questions (optional)"), "10");
		await user.click(within(dlg).getByRole("button", { name: "Run evaluation" }));
		await waitFor(() => expect(calls(mock.log, "POST", "/api/v1/admin/eval/trigger")).toHaveLength(1));
		expect(calls(mock.log, "POST", "/api/v1/admin/eval/trigger")[0]?.body).toEqual({
			corpus_id: PATIENT,
			sample_size: 10,
		});
		expect(await screen.findByTestId("run-polling")).toBeInTheDocument();
		await waitFor(() => expect(screen.queryByTestId("run-polling")).toBeNull(), { timeout: 8000 });
		expect(screen.getByText("No scores were recorded: the golden set may be empty.")).toBeInTheDocument();
	}, 15_000);

	it("admin: a finished run explains each metric and marks the target", async () => {
		open(`/quality?kb=${PATIENT}&tab=runs&traffic=${EVAL_DONE}`, "admin");
		const scores = await screen.findByRole("table", { name: "Scores" });
		expect(within(scores).getByText("Faithfulness")).toBeInTheDocument();
		expect(within(scores).getByText(/stick to what the documents say/)).toBeInTheDocument();
		expect(within(scores).getByText("Meets target")).toBeInTheDocument();
		expect(within(scores).getByText("Below target")).toBeInTheDocument();
		expect(within(scores).getByText("0.61")).toBeInTheDocument();
	});

	it("admin: optimisation detail copies per-KB settings into the Retrieval tab without saving", async () => {
		const { user, router } = open(`/quality?kb=${PATIENT}&tab=optimize&run=${OPT_DONE}`, "admin");
		expect(await screen.findByRole("region", { name: "Suggested settings" })).toBeInTheDocument();
		expect(screen.getByTestId("engine-wide-params")).toHaveTextContent(
			"Keyword-search candidates, Recency half-life (days), Passages sent to the re-ranker",
		);
		expect(screen.getByRole("table", { name: "Best trade-offs" })).toBeInTheDocument();
		const puts: string[] = [];
		server.events.on("request:start", ({ request }) => {
			if (request.method === "PUT") puts.push(request.url);
		});
		await user.click(screen.getByRole("button", { name: "Copy to Retrieval tab" }));
		await waitFor(() => expect(router.state.location.pathname).toBe(`/kbs/${PATIENT}`));
		expect(router.state.location.search).toMatchObject({ tab: "retrieval" });
		expect(await screen.findByText("Suggested by an optimisation run — review and save.")).toBeInTheDocument();
		await waitFor(() => expect(screen.getByLabelText("Passages searched")).toHaveValue("42"));
		expect(screen.getByLabelText("Passages given to the model")).toHaveValue("6");
		expect(puts).toHaveLength(0);
		server.events.removeAllListeners();
	});

	it("admin: a run over every knowledge base asks which one to copy to", async () => {
		const { mock, user } = open(`/quality?kb=${PATIENT}&tab=optimize`, "admin");
		await user.click(await screen.findByRole("button", { name: /Open run from 28 Sept? 2026/ }));
		expect(await screen.findByText(/pick which one to copy the settings to/)).toBeInTheDocument();
		expect(screen.getByLabelText("Knowledge base")).toHaveValue(PATIENT);
		expect(calls(mock.log, "GET", `/api/v1/admin/optimize/runs/${OPT_ALL}`)).toHaveLength(1);
	});
});
