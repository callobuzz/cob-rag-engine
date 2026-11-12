import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { BASE, PRIYA, SHREE } from "../msw/data";
import { assistantStateFor, DRAFT_CONFIG, IMPROVED_RULE } from "../msw/handlers/assistant";
import { server } from "../msw/server";
import { capturePuts, openAssistant, PATIENT, signedInAs } from "./assistant-utils";

type Sent = {
	base_revision: number;
	config: { enabled: boolean; identity: { card: { phone: string } }; voice: unknown };
};

describe("Identity section", () => {
	it("validates the phone live, saves it normalised and keeps the other sections", async () => {
		signedInAs(PRIYA, SHREE);
		const sent = capturePuts();
		const { user } = await openAssistant("identity");
		const phone = await screen.findByRole("textbox", { name: "Phone" });
		await user.clear(phone);
		await user.type(phone, "12345");
		await user.tab();
		expect(await screen.findByText(/Enter a 10-digit Indian number/)).toBeInTheDocument();
		await user.clear(phone);
		await user.type(phone, "98220 12345");
		await waitFor(() => expect(screen.queryByText(/Enter a 10-digit Indian number/)).toBeNull());
		expect(screen.getAllByText("Saved as +91 98220 12345").length).toBeGreaterThan(0);
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		await waitFor(() => expect(sent).toHaveLength(1));
		const body = sent[0] as unknown as Sent;
		expect(body.config.identity.card.phone).toBe("+919822012345");
		expect(body.base_revision).toBe(3);
		expect(body.config.voice).toEqual((DRAFT_CONFIG as unknown as Sent["config"]).voice);
		expect(await screen.findByText("Draft saved. Preview it, then publish when ready.")).toBeInTheDocument();
		expect(assistantStateFor(PATIENT).draft?.draft_revision).toBe(4);
	});

	it("opening hours: equal opening and closing times are refused on that field", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("identity");
		const close = await screen.findByLabelText("Monday closes (range 1)");
		fireEvent.change(close, { target: { value: "07:00" } });
		fireEvent.blur(close);
		expect(await screen.findByText("Closing time must differ from opening time.")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		expect(assistantStateFor(PATIENT).draft?.draft_revision).toBe(3);
	});

	it("a day can get a second range and a closed day shows Closed", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("identity");
		await user.click(await screen.findByRole("button", { name: "Add hours for Sunday" }));
		expect(await screen.findByLabelText("Sunday opens (range 1)")).toHaveValue("09:00");
		await user.click(screen.getByRole("button", { name: "Add hours for Monday" }));
		expect(await screen.findByLabelText("Monday opens (range 2)")).toBeInTheDocument();
	});

	it("a draft conflict offers to load the other version and never overwrites", async () => {
		server.use(
			http.put(`${BASE}/api/v1/corpora/${PATIENT}/assistant/draft`, () =>
				HttpResponse.json({ detail: "x", code: "assistant_draft_conflict", current_revision: 9 }, { status: 409 }),
			),
		);
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("identity");
		const name = await screen.findByRole("textbox", { name: "Assistant name" });
		await user.clear(name);
		await user.type(name, "Meera");
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		expect(await screen.findByRole("button", { name: "Load their version" })).toBeInTheDocument();
		expect(assistantStateFor(PATIENT).draft?.draft_revision).toBe(3);
	});

	it("an engine 422 lands on the field it names", async () => {
		server.use(
			http.put(`${BASE}/api/v1/corpora/${PATIENT}/assistant/draft`, () =>
				HttpResponse.json(
					{
						detail: [
							{ loc: ["body", "config", "identity", "business_name"], msg: "Too long for the engine", type: "x" },
						],
					},
					{ status: 422 },
				),
			),
		);
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("identity");
		const biz = await screen.findByRole("textbox", { name: "Business name" });
		await user.type(biz, " Pune");
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		expect(await screen.findByText("Too long for the engine")).toBeInTheDocument();
	});

	it("leaving with unsaved edits asks to discard", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("identity");
		await user.type(await screen.findByRole("textbox", { name: "Business name" }), " Pune");
		const nav = screen.getByRole("navigation", { name: "Assistant settings" });
		await user.click(within(nav).getByRole("link", { name: "Voice" }));
		expect(await screen.findByRole("alertdialog", { name: "Discard changes?" })).toBeInTheDocument();
	});

	it("Publish waits while the form has unsaved edits", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("identity");
		await user.type(await screen.findByRole("textbox", { name: "Business name" }), " Pune");
		expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
		expect(screen.getByText(/Save the section you are editing first/)).toBeInTheDocument();
	});
});

describe("Voice and Language", () => {
	it("format preferences and reply length are saved with the voice", async () => {
		signedInAs(PRIYA, SHREE);
		const sent = capturePuts();
		const { user } = await openAssistant("voice");
		const tables = await screen.findByRole("radiogroup", { name: "Tables" });
		await user.click(within(tables).getByRole("radio", { name: "Never" }));
		await user.click(screen.getByRole("radio", { name: "Short (about 40 words)" }));
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		await waitFor(() => expect(sent).toHaveLength(1));
		const voice = (sent[0] as { config: { voice: { response_length: string; format: { tables: string } } } }).config
			.voice;
		expect(voice.response_length).toBe("short");
		expect(voice.format.tables).toBe("never");
	});

	it("preferred words, banned phrases and examples have limits", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("voice");
		await user.click(await screen.findByRole("button", { name: "Add a pair" }));
		await user.type(screen.getByLabelText("Word to say 1"), "booking");
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		expect(await screen.findByText("Write the word to replace.")).toBeInTheDocument();
		for (let i = 0; i < 5; i++) await user.click(screen.getByRole("button", { name: "Add an example" }));
		expect(screen.getByRole("button", { name: "Add an example" })).toBeDisabled();
	});

	it("warns when Hindi is on but the search model is English-only, without naming a model", async () => {
		server.use(
			http.get(`${BASE}/api/v1/models/embeddings`, () =>
				HttpResponse.json([
					{
						id: "bge-m3",
						provider: "tei",
						model: "BAAI/bge-m3",
						name: "KB model",
						dimensions: 1024,
						multilingual: false,
						multilingual_source: "https://example.org/card",
					},
				]),
			),
		);
		signedInAs(PRIYA, SHREE);
		await openAssistant("language");
		const warning = await screen.findByText(/Your search model is English-only/);
		expect(warning.textContent).not.toMatch(/bge|BAAI/i);
		expect(screen.getByRole("link", { name: "Embedding" })).toHaveAttribute(
			"href",
			expect.stringContaining("tab=embedding"),
		);
	});

	it("no warning when the model is documented as multilingual", async () => {
		signedInAs(PRIYA, SHREE);
		await openAssistant("language");
		await screen.findByRole("checkbox", { name: "Hinglish (Hindi in English letters)" });
		await waitFor(() => expect(screen.queryByText(/English-only/)).toBeNull());
	});

	it("the main language must stay among the supported ones", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("language");
		await user.click(await screen.findByRole("checkbox", { name: "English" }));
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		expect(await screen.findByText("The main language must be one of the supported languages.")).toBeInTheDocument();
	});
});

describe("Topics and limits", () => {
	it("custom search confidence is bounded 0 to 1 and saved; the default sends null", async () => {
		signedInAs(PRIYA, SHREE);
		const sent = capturePuts();
		const { user } = await openAssistant("scope");
		await user.click(await screen.findByRole("radio", { name: "Custom" }));
		const input = screen.getByRole("spinbutton", { name: "Minimum search confidence" });
		await user.clear(input);
		await user.type(input, "1.4");
		await user.tab();
		expect(await screen.findByText("Use a number from 0 to 1.")).toBeInTheDocument();
		await user.clear(input);
		await user.type(input, "0.42");
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		await waitFor(() => expect(sent).toHaveLength(1));
		expect((sent[0] as { config: { scope: { min_retrieval_score: number } } }).config.scope.min_retrieval_score).toBe(
			0.42,
		);
		await user.click(screen.getByRole("radio", { name: "Measured default for your search model" }));
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		await waitFor(() => expect(sent).toHaveLength(2));
		expect(
			(sent[1] as { config: { scope: { min_retrieval_score: null } } }).config.scope.min_retrieval_score,
		).toBeNull();
	});

	it("a new topic gets an exact answer option and an id from its name", async () => {
		signedInAs(PRIYA, SHREE);
		const sent = capturePuts();
		const { user } = await openAssistant("scope");
		await user.click(await screen.findByRole("button", { name: "Add topic" }));
		const names = screen.getAllByRole("textbox", { name: /^Topic name \(\d+\)$/ });
		const last = names[names.length - 1] as HTMLElement;
		await user.type(last, "Fasting rules");
		await user.tab();
		expect(screen.getByRole("checkbox", { name: "Use an exact answer for Fasting rules" })).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		await waitFor(() => expect(sent).toHaveLength(1));
		const topics = (sent[0] as { config: { scope: { topics: { id: string; name: string }[] } } }).config.scope.topics;
		expect(topics.at(-1)).toMatchObject({ id: "fasting-rules", name: "Fasting rules" });
	});

	it("choosing a sensitive field fills the reminder; turning the fact check off warns", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("scope");
		await user.selectOptions(await screen.findByRole("combobox", { name: /sensitive field/i }), "medical");
		expect(await screen.findByDisplayValue(/not medical advice/)).toBeInTheDocument();
		await user.click(screen.getByRole("radio", { name: "Off" }));
		expect(await screen.findByText(/may repeat a wrong price/)).toBeInTheDocument();
	});

	it("a topic id shared with a blocked topic is refused", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("scope");
		await user.click(await screen.findByRole("button", { name: "Add blocked topic" }));
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		expect(await screen.findByText("Name the topic.")).toBeInTheDocument();
	});
});

describe("Guidance rules", () => {
	const thirtyRules = () => {
		const rules = Array.from({ length: 30 }, (_, i) => ({
			id: `r${i}`,
			type: "communication",
			when: null,
			instruction: `Rule ${i}`,
			enabled: true,
			priority: 50,
			source: "owner",
		}));
		const s = assistantStateFor(PATIENT);
		return http.get(`${BASE}/api/v1/corpora/${PATIENT}/assistant`, () =>
			HttpResponse.json({
				corpus_id: PATIENT,
				enabled: true,
				schema_version: 1,
				published: s.published,
				draft: s.draft ? { ...s.draft, config: { ...s.draft.config, rules } } : null,
			}),
		);
	};

	it("counts the rules on and refuses a 31st", async () => {
		server.use(thirtyRules());
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("rules");
		expect(await screen.findByText("30 of 30 rules on")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Add rule" }));
		await user.type(await screen.findByRole("textbox", { name: "Instruction" }), "Always say aap, never tum.");
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		expect(await screen.findByText("Only 30 rules can be on at once.")).toBeInTheDocument();
	});

	it("shows the length meter from the engine's numbers and warns when over", async () => {
		server.use(
			http.post(`${BASE}/api/v1/corpora/${PATIENT}/assistant/compile`, () =>
				HttpResponse.json({
					system_prompt: "x",
					constraints: "",
					sections: [],
					rules_fired: [],
					total_tokens: 2000,
					max_tokens: 300,
					enabled_rules: 9,
					max_enabled_rules: 30,
					rules_tokens: 1450,
					rules_budget: 1200,
					prompt_tokens: 2000,
					prompt_budget: 3000,
				}),
			),
		);
		signedInAs(PRIYA, SHREE);
		await openAssistant("rules");
		expect(await screen.findByText(/Publishing will be refused until the rules are shorter/)).toBeInTheDocument();
		expect(screen.getByRole("meter", { name: "Rules length" })).toHaveAttribute(
			"aria-valuetext",
			expect.stringContaining("1,450 of 1,200"),
		);
	});

	it("editing a template rule makes it yours; conditions left empty stay empty", async () => {
		signedInAs(PRIYA, SHREE);
		const sent = capturePuts();
		const { user } = await openAssistant("rules");
		await user.click(await screen.findByRole("button", { name: "Edit rule 1" }));
		const box = await screen.findByRole("textbox", { name: "Instruction" });
		await user.type(box, " Thank you.");
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		await waitFor(() => expect(sent).toHaveLength(1));
		const rules = (sent[0] as { config: { rules: { source: string; when: unknown; instruction: string }[] } }).config
			.rules;
		const first = rules[0];
		expect(first?.source).toBe("owner");
		expect(first?.instruction).toMatch(/Thank you\.$/);
		expect(rules).toHaveLength(9);
	});

	it("Improve fills the form with the suggestion only when used, and the section's Save is the one write", async () => {
		signedInAs(PRIYA, SHREE);
		const sent = capturePuts();
		const { user } = await openAssistant("rules");
		await user.click(await screen.findByRole("button", { name: "Improve rule 1" }));
		const dialog = await screen.findByRole("dialog", { name: "Improve this rule" });
		expect(await within(dialog).findByText(IMPROVED_RULE)).toBeInTheDocument();
		expect(sent).toHaveLength(0); // asking saves nothing
		await user.click(within(dialog).getByRole("button", { name: "Keep my wording" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		await user.click(screen.getByRole("button", { name: "Improve rule 1" }));
		await user.click(await screen.findByRole("button", { name: "Use this wording" }));
		await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
		expect(sent).toHaveLength(0); // using it only fills the form
		expect(screen.getByText(IMPROVED_RULE)).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		await waitFor(() => expect(sent).toHaveLength(1));
		const first = (sent[0] as { config: { rules: { source: string; instruction: string }[] } }).config.rules[0];
		expect(first).toMatchObject({ instruction: IMPROVED_RULE, source: "owner" });
	});

	it("deleting a rule needs a confirmation", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("rules");
		await user.click(await screen.findByRole("button", { name: "Delete rule 1" }));
		await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete rule" }));
		await waitFor(() => expect(screen.queryByRole("button", { name: "Delete rule 9" })).toBeNull());
	});
});

describe("Conversation, leads and talk-to-a-person settings", () => {
	it("starters are limited to four and an empty label is refused", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("conversation");
		expect(await screen.findByRole("button", { name: "Add starter" })).toBeDisabled();
		await user.click(screen.getByRole("button", { name: "Remove starter 4" }));
		await user.click(screen.getByRole("button", { name: "Add starter" }));
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		expect(await screen.findAllByText("Write the button text.")).not.toHaveLength(0);
	});

	it("a link as the buying next step asks for its address and text", async () => {
		signedInAs(PRIYA, SHREE);
		const sent = capturePuts();
		const { user } = await openAssistant("conversation");
		await user.selectOptions(await screen.findByRole("combobox", { name: /buying interest/ }), "link");
		await user.type(screen.getByRole("textbox", { name: "Link address" }), "https://shree-labs.in/book");
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		await waitFor(() => expect(sent).toHaveLength(1));
		const cta = (sent[0] as { config: { conversation: { cta: { buying_intent_cta: string; link_url: string } } } })
			.config.conversation.cta;
		expect(cta).toMatchObject({ buying_intent_cta: "link", link_url: "https://shree-labs.in/book" });
	});

	it("leads: a form with neither phone nor e-mail required is refused", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("leads");
		const phone = await screen.findByRole("radiogroup", { name: "Phone" });
		await user.click(within(phone).getByRole("radio", { name: "Optional" }));
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		expect(await screen.findByText("Phone or e-mail must be required.")).toBeInTheDocument();
	});

	it("leads: the notice version is shown, never edited, and the saved body keeps it", async () => {
		signedInAs(PRIYA, SHREE);
		const sent = capturePuts();
		const { user } = await openAssistant("leads");
		expect(await screen.findByText(/Notice version 1/)).toBeInTheDocument();
		await user.click(screen.getByRole("switch", { name: "Send a daily summary" }));
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		await waitFor(() => expect(sent).toHaveLength(1));
		const leads = (
			sent[0] as { config: { leads: { consent: { notice_version: number }; notify: { daily_digest: boolean } } } }
		).config.leads;
		expect(leads.consent.notice_version).toBe(1);
		expect(leads.notify.daily_digest).toBe(true);
	});

	it("leads: qualification rules are added, edited and saved in the engine's own shape", async () => {
		signedInAs(PRIYA, SHREE);
		const sent = capturePuts();
		const { user } = await openAssistant("leads");
		await user.click(await screen.findByRole("button", { name: "Add a rule" }));
		expect(screen.getByRole("combobox", { name: "Rule 1 marks the lead as" })).toHaveValue("qualified");
		// The first condition starts as "why they left their details" equal to a buying question.
		expect(screen.getByRole("combobox", { name: "Condition 1: value" })).toHaveValue("buying_intent");
		await user.click(screen.getByRole("button", { name: "Add a condition to rule 1" }));
		await user.selectOptions(
			screen.getAllByRole("combobox", { name: "Condition 2: about" })[0] as HTMLElement,
			"phone",
		);
		await user.selectOptions(screen.getAllByRole("combobox", { name: "Condition 2: is" })[0] as HTMLElement, "present");
		expect(screen.queryByLabelText("Condition 2: value")).toBeNull(); // "given" needs no value
		await user.click(screen.getByRole("button", { name: "Add a rule" }));
		await user.selectOptions(screen.getByRole("combobox", { name: "Rule 2 marks the lead as" }), "partial");
		await user.selectOptions(screen.getAllByRole("combobox", { name: "Condition 1: is" })[1] as HTMLElement, "in");
		await user.clear(screen.getAllByRole("textbox", { name: "Condition 1: value" })[0] as HTMLElement);
		await user.type(
			screen.getAllByRole("textbox", { name: "Condition 1: value" })[0] as HTMLElement,
			"asked, callback",
		);
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		await waitFor(() => expect(sent).toHaveLength(1));
		const rules = (sent[0] as { config: { leads: { qualification: unknown[] } } }).config.leads.qualification;
		expect(rules).toEqual([
			{
				status: "qualified",
				conditions: [
					{ field: "intent", op: "eq", value: "buying_intent" },
					{ field: "phone", op: "present", value: null },
				],
			},
			{ status: "partial", conditions: [{ field: "intent", op: "in", value: ["asked", "callback"] }] },
		]);
	});

	it("leads: a condition without a value, or a number rule with text, is refused on that field", async () => {
		signedInAs(PRIYA, SHREE);
		const sent = capturePuts();
		const { user } = await openAssistant("leads");
		await user.click(await screen.findByRole("button", { name: "Add a rule" }));
		await user.selectOptions(screen.getByRole("combobox", { name: "Condition 1: about" }), "name");
		await user.selectOptions(screen.getByRole("combobox", { name: "Condition 1: is" }), "gte");
		await user.type(screen.getByRole("textbox", { name: "Condition 1: value" }), "lots");
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		expect(await screen.findByText("Enter a number.")).toBeInTheDocument();
		expect(sent).toHaveLength(0);
	});

	it("leads: removing the last rule leaves the leads unmarked", async () => {
		signedInAs(PRIYA, SHREE);
		const sent = capturePuts();
		const { user } = await openAssistant("leads");
		await user.click(await screen.findByRole("button", { name: "Add a rule" }));
		await user.click(screen.getByRole("button", { name: "Remove rule 1" }));
		expect(screen.getByText("No rules yet: leads are not marked.")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		await waitFor(() => expect(sent).toHaveLength(1));
		expect((sent[0] as { config: { leads: { qualification: unknown[] } } }).config.leads.qualification).toEqual([]);
	});

	it("publishing with leads on and no encryption key explains the fix next to the leads switch", async () => {
		server.use(
			http.post(`${BASE}/api/v1/corpora/${PATIENT}/assistant/publish`, () =>
				HttpResponse.json({ detail: "x", code: "encryption_not_configured" }, { status: 503 }),
			),
		);
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("leads");
		await user.click(await screen.findByRole("button", { name: "Publish" }));
		await user.click(screen.getByRole("button", { name: "Publish now" }));
		expect(
			await screen.findAllByText("Leads need an encryption key on the engine before they can be switched on."),
		).toHaveLength(2);
	});

	it("handoff channel addresses fall back to the business card", async () => {
		signedInAs(PRIYA, SHREE);
		await openAssistant("handoff");
		expect(await screen.findByRole("textbox", { name: "WhatsApp number" })).toHaveAttribute(
			"placeholder",
			"+91 98220 12345",
		);
		expect(screen.getByRole("textbox", { name: "E-mail address" })).toHaveAttribute(
			"placeholder",
			"care@shree-labs.in",
		);
	});

	it("handoff: an optional trigger can be switched off, and a way to reach you added", async () => {
		signedInAs(PRIYA, SHREE);
		const sent = capturePuts();
		const { user } = await openAssistant("handoff");
		await user.click(await screen.findByRole("switch", { name: "After unanswered questions" }));
		await user.selectOptions(screen.getByRole("combobox", { name: "Add a way to reach you" }), "booking");
		await user.click(screen.getByRole("button", { name: "Add a way to reach you" }));
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		await waitFor(() => expect(sent).toHaveLength(1));
		const h = (
			sent[0] as {
				config: { handoff: { triggers: { unresolved_turns: number | null }; channels: { kind: string }[] } };
			}
		).config.handoff;
		expect(h.triggers.unresolved_turns).toBeNull();
		expect(h.channels.map((c) => c.kind)).toContain("booking");
	});
});

describe("Expert and versions", () => {
	it("shows the built instructions read-only, then unlocks expert instructions", async () => {
		signedInAs(PRIYA, SHREE);
		const sent = capturePuts();
		const { user } = await openAssistant("advanced");
		expect(await screen.findByRole("heading", { name: "Grounding and safety" })).toBeInTheDocument();
		expect(screen.getByText(/Worst case: 570 of 3,000 tokens/)).toBeInTheDocument();
		expect(screen.getByText(/fasting-exact/)).toBeInTheDocument();
		expect(screen.queryByRole("textbox", { name: "Expert instructions" })).toBeNull();
		await user.click(screen.getByRole("button", { name: "Edit expert instructions" }));
		const box = screen.getByRole("textbox", { name: "Expert instructions" });
		await user.type(box, "Always greet patients by name.");
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		await waitFor(() => expect(sent).toHaveLength(1));
		expect(
			(sent[0] as { config: { advanced: { extra_instructions: string } } }).config.advanced.extra_instructions,
		).toBe("Always greet patients by name.");
	});

	it("expert instructions are limited to 15,000 characters", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("advanced");
		await user.click(await screen.findByRole("button", { name: "Edit expert instructions" }));
		const box = screen.getByRole("textbox", { name: "Expert instructions" });
		fireEvent.change(box, { target: { value: "x".repeat(15_001) } });
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		expect(await screen.findByText("At most 15,000 characters.")).toBeInTheDocument();
	});

	it("owners restore an older version after confirming; the switch stays as it is", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("versions");
		const row = await screen.findByRole("row", { name: /Version 2/ });
		await user.click(within(row).getByRole("button", { name: "Restore" }));
		expect(await screen.findByText(/stays on as it is now/)).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Restore version 2" }));
		expect(await screen.findByText(/Restored version 2 as version 4/)).toBeInTheDocument();
		expect(assistantStateFor(PATIENT).published?.restored_from).toBe(2);
	});

	it("the live version has no Restore, and View shows what differs from live", async () => {
		signedInAs(PRIYA, SHREE);
		const { user } = await openAssistant("versions");
		const live = await screen.findByRole("row", { name: /Version 3/ });
		expect(within(live).queryByRole("button", { name: "Restore" })).toBeNull();
		await user.click(
			within(await screen.findByRole("row", { name: /Version 1/ })).getByRole("button", { name: "View" }),
		);
		expect(await screen.findByRole("dialog", { name: "Version 1" })).toBeInTheDocument();
		expect(await screen.findByText(/Compared with the live version/)).toBeInTheDocument();
	});
});

// TV2 reference
describe("recommended consent wording", () => {
	it("offers the new wording when the notice differs, and replaces it only on a click", async () => {
		signedInAs(PRIYA, SHREE);
		const sent = capturePuts();
		const { user } = await openAssistant("leads");
		expect(await screen.findByText(/A new recommended wording is available/)).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Use the recommended wording" }));
		expect(screen.queryByText(/A new recommended wording is available/)).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Save draft" }));
		await waitFor(() => expect(sent).toHaveLength(1));
		const notice = (sent[0] as { config: { leads: { consent: { notice: Record<string, string> } } } }).config.leads
			.consent.notice;
		expect(notice.en).toContain("a short summary of this chat and a reference code");
		expect(notice["hi-Latn"]).toContain("reference code");
	});
});
// /TV2 reference
