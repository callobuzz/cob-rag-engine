import { describe, expect, it, vi } from "vitest";
import { checkFields, ENDPOINT, initContact, type Messages, payloadOf, send } from "./contact";

const text: Messages = {
	invalid: "Please check the form and try again.",
	rate: "Too many.",
	unavailable: "Not working.",
	required: "Needed.",
	reach: "Give an e-mail or phone.",
	sending: "Sending...",
	done: "Thank you.",
};

function form(): HTMLFormElement {
	document.body.innerHTML = `
	<form data-contact data-lang="hi-Latn" data-msg-invalid="${text.invalid}" data-msg-rate="${text.rate}"
	  data-msg-unavailable="${text.unavailable}" data-msg-required="${text.required}" data-msg-reach="${text.reach}"
	  data-sending="${text.sending}" data-done="${text.done}">
	  <p data-status hidden></p>
	  <div class="field"><input name="name" value="Priya"></div>
	  <div class="field"><input name="email" value=""></div>
	  <div class="field"><input name="phone" value=""></div>
	  <select name="topic"><option value="sales" selected>s</option></select>
	  <div class="field"><textarea name="message">Can it answer in Marathi too?</textarea></div>
	  <input name="website" value="">
	  <input type="hidden" name="form_token" value="123.sig">
	  <input type="checkbox" name="consent" value="1" checked>
	  <button data-submit>Send</button>
	</form>`;
	return document.querySelector("form") as HTMLFormElement;
}

describe("the form's own checks", () => {
	it("need a name, a message of ten letters, a way to reply and the consent", () => {
		expect(checkFields({ name: "", message: "short", email: "", phone: "" }, false, text)).toEqual({
			name: "Needed.",
			message: "Needed.",
			email: "Give an e-mail or phone.",
			consent: "Needed.",
		});
		expect(
			checkFields({ name: "P", message: "long enough text", email: "", phone: "98220 12345" }, true, text),
		).toEqual({});
	});
	it("builds the JSON the server expects", () => {
		const payload = payloadOf(
			{ name: " Priya ", email: "", phone: "98220 12345", message: " Hello there friend ", form_token: "t" },
			"hi",
		);
		expect(payload).toMatchObject({
			name: "Priya",
			email: null,
			phone: "98220 12345",
			consent: true,
			lang: "hi",
			topic: "sales",
			website: "",
		});
	});
});

describe("sending", () => {
	const post = (status: number): typeof fetch =>
		vi.fn(async () => new Response("{}", { status })) as unknown as typeof fetch;
	it("maps every answer to one sentence", async () => {
		expect(await send({}, text, post(202))).toEqual({ ok: true, message: "Thank you." });
		expect((await send({}, text, post(429))).message).toBe("Too many.");
		expect((await send({}, text, post(503))).message).toBe("Not working.");
		expect((await send({}, text, post(422))).message).toBe(text.invalid);
		const failing = vi.fn(async () => {
			throw new Error("offline");
		}) as unknown as typeof fetch;
		expect((await send({}, text, failing)).message).toBe("Not working.");
	});
	it("posts to the public route as JSON", async () => {
		const spy = vi.fn(async () => new Response("{}", { status: 202 }));
		await send({ a: 1 }, text, spy as unknown as typeof fetch);
		expect(spy).toHaveBeenCalledWith(ENDPOINT, expect.objectContaining({ method: "POST" }));
	});
});

describe("the enhanced form", () => {
	it("shows field errors and keeps what was typed", async () => {
		const f = form();
		(f.querySelector('[name="phone"]') as HTMLInputElement).value = "";
		initContact(document);
		f.dispatchEvent(new Event("submit", { cancelable: true }));
		await Promise.resolve();
		expect(f.querySelector('[name="email"]')?.getAttribute("aria-invalid")).toBe("true");
		expect((f.querySelector('[name="name"]') as HTMLInputElement).value).toBe("Priya");
	});

	it("keeps the typed text and shows the sentence when the server says no", async () => {
		const f = form();
		(f.querySelector('[name="phone"]') as HTMLInputElement).value = "98220 12345";
		globalThis.fetch = vi.fn(async () => new Response("{}", { status: 429 })) as unknown as typeof fetch;
		initContact(document);
		f.dispatchEvent(new Event("submit", { cancelable: true }));
		await vi.waitFor(() => expect(f.querySelector("[data-status]")?.textContent).toBe("Too many."));
		expect((f.querySelector('[name="message"]') as HTMLTextAreaElement).value).toContain("Marathi");
	});

	it("replaces the form with the thank-you on success", async () => {
		const f = form();
		(f.querySelector('[name="phone"]') as HTMLInputElement).value = "98220 12345";
		globalThis.fetch = vi.fn(async () => new Response("{}", { status: 202 })) as unknown as typeof fetch;
		initContact(document);
		f.dispatchEvent(new Event("submit", { cancelable: true }));
		await vi.waitFor(() => expect(f.textContent).toContain("Thank you."));
	});
});
