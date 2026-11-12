/**
 * The contact form, enhanced. Without JavaScript the form posts to `/contact` and the server
 * answers a page; here it posts JSON to the public route and shows the answer in place. Typed text
 * is never cleared on a failure. Every anti-spam failure is the same 422 and the same sentence.
 */
export const ENDPOINT = "/api/v1/public/landing/contact";

export type Messages = {
	invalid: string;
	rate: string;
	unavailable: string;
	required: string;
	reach: string;
	sending: string;
	done: string;
};

export function messagesOf(form: HTMLElement): Messages {
	const d = form.dataset;
	return {
		invalid: d.msgInvalid ?? "",
		rate: d.msgRate ?? "",
		unavailable: d.msgUnavailable ?? "",
		required: d.msgRequired ?? "",
		reach: d.msgReach ?? "",
		sending: d.sending ?? "",
		done: d.done ?? "",
	};
}

const REQUIRED = ["name", "message"] as const;

/** Field problems found before sending: the same rules as the server, in the visitor's language. */
export function checkFields(values: Record<string, string>, consent: boolean, text: Messages): Record<string, string> {
	const errors: Record<string, string> = {};
	for (const name of REQUIRED) if (!(values[name] ?? "").trim()) errors[name] = text.required;
	if ((values.message ?? "").trim().length > 0 && (values.message ?? "").trim().length < 10)
		errors.message = text.required;
	if (!(values.email ?? "").trim() && !(values.phone ?? "").trim()) errors.email = text.reach;
	if (!consent) errors.consent = text.required;
	return errors;
}

export function payloadOf(values: Record<string, string>, lang: string): Record<string, unknown> {
	const orNull = (v: string | undefined): string | null => (v?.trim() ? v.trim() : null);
	return {
		name: (values.name ?? "").trim(),
		email: orNull(values.email),
		phone: orNull(values.phone),
		topic: values.topic || "sales",
		message: (values.message ?? "").trim(),
		consent: true,
		website: values.website ?? "",
		form_token: values.form_token ?? "",
		lang,
		service: orNull(values.service),
	};
}

export async function send(
	payload: Record<string, unknown>,
	text: Messages,
	post: typeof fetch = fetch,
): Promise<{ ok: boolean; message: string }> {
	let res: Response;
	try {
		res = await post(ENDPOINT, {
			method: "POST",
			headers: { "content-type": "application/json" },
			body: JSON.stringify(payload),
		});
	} catch {
		return { ok: false, message: text.unavailable };
	}
	if (res.status === 202) return { ok: true, message: text.done };
	if (res.status === 429) return { ok: false, message: text.rate };
	if (res.status === 503) return { ok: false, message: text.unavailable };
	return { ok: false, message: text.invalid };
}

function show(form: HTMLElement, errors: Record<string, string>): void {
	for (const el of form.querySelectorAll<HTMLElement>(".field-error[data-js]")) el.remove();
	for (const input of form.querySelectorAll<HTMLElement>("[aria-invalid]")) input.removeAttribute("aria-invalid");
	for (const [name, message] of Object.entries(errors)) {
		const input = form.querySelector<HTMLElement>(`[name="${name}"]`);
		if (!input) continue;
		input.setAttribute("aria-invalid", "true");
		const note = document.createElement("p");
		note.className = "field-error";
		note.dataset.js = "1";
		note.textContent = message;
		(input.closest(".field") ?? input.parentElement)?.append(note);
	}
}

export function initContact(doc: Document = document): void {
	const form = doc.querySelector<HTMLFormElement>("[data-contact]");
	if (!form) return;
	const status = form.querySelector<HTMLElement>("[data-status]");
	const button = form.querySelector<HTMLButtonElement>("[data-submit]");
	const text = messagesOf(form);
	const say = (message: string): void => {
		if (!status) return;
		status.textContent = message;
		status.hidden = message === "";
	};
	form.addEventListener("submit", async (event) => {
		event.preventDefault();
		const data = new FormData(form);
		const values: Record<string, string> = {};
		for (const [key, value] of data.entries()) values[key] = typeof value === "string" ? value : "";
		const errors = checkFields(values, data.has("consent"), text);
		show(form, errors);
		if (Object.keys(errors).length > 0) {
			say("");
			form.querySelector<HTMLElement>("[aria-invalid]")?.focus();
			return;
		}
		const original = button?.textContent ?? "";
		if (button) {
			button.disabled = true;
			button.textContent = text.sending;
		}
		const outcome = await send(payloadOf(values, form.dataset.lang ?? "en"), text);
		if (button) {
			button.disabled = false;
			button.textContent = original;
		}
		if (outcome.ok) {
			form.replaceChildren();
			const done = doc.createElement("p");
			done.className = "form-status";
			done.setAttribute("role", "status");
			done.textContent = outcome.message;
			form.append(done);
			return;
		}
		say(outcome.message);
	});
}
