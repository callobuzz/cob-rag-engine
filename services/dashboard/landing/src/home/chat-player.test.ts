import { describe, expect, it, vi } from "vitest";
import { play } from "./chat-player";

function hero(): HTMLElement {
	document.body.innerHTML = `
	<section data-section="hero"><div class="chat" data-chat>
	  <p class="msg" data-role="visitor">Fees kitni hai?</p>
	  <p class="msg" data-role="assistant">Consultation ₹500 hai. <span class="chip">From: Fees page</span></p>
	  <div class="lead" data-lead>Priya S.</div>
	</div></section>`;
	return document.querySelector("[data-chat]") as HTMLElement;
}

describe("chat player", () => {
	it("does nothing without motion-ok: everything stays visible", () => {
		const chat = hero();
		play(chat, { motion: false });
		expect([...chat.querySelectorAll(".msg")].every((m) => !m.classList.contains("is-pending"))).toBe(true);
	});

	it("reveals in order and finishes within four seconds", () => {
		vi.useFakeTimers();
		const chat = hero();
		play(chat, { motion: true });
		expect(chat.querySelector('[data-role="assistant"]')?.classList.contains("is-pending")).toBe(true);
		vi.advanceTimersByTime(4000);
		expect(chat.querySelectorAll(".is-pending").length).toBe(0);
		vi.useRealTimers();
	});

	it("finishes at once on scroll, key or pointer", () => {
		for (const name of ["scroll", "keydown", "pointerdown"]) {
			vi.useFakeTimers();
			const chat = hero();
			play(chat, { motion: true });
			expect(chat.querySelectorAll(".is-pending").length).toBeGreaterThan(0);
			window.dispatchEvent(new Event(name));
			expect(chat.querySelectorAll(".is-pending").length).toBe(0);
			vi.useRealTimers();
		}
	});

	it("never changes the text: the HTML already holds the final words", () => {
		const chat = hero();
		const before = chat.textContent;
		play(chat, { motion: true });
		expect(chat.textContent).toBe(before);
	});

	it("keeps a long conversation inside the time budget", () => {
		vi.useFakeTimers();
		document.body.innerHTML = `<div data-chat>${Array.from(
			{ length: 3 },
			() =>
				`<li class="msg" data-role="visitor"><p>${"word ".repeat(30)}</p></li><li class="msg" data-role="assistant"><p>${"word ".repeat(60)}</p></li>`,
		).join("")}</div>`;
		const chat = document.querySelector("[data-chat]") as HTMLElement;
		play(chat, { motion: true });
		vi.advanceTimersByTime(4000);
		expect(chat.querySelectorAll(".is-pending").length).toBe(0);
		vi.useRealTimers();
	});

	it("wraps each word of an answer without changing a character", () => {
		document.body.innerHTML = `<div data-chat><li class="msg" data-role="assistant"><p>One two  three</p></li></div>`;
		const chat = document.querySelector("[data-chat]") as HTMLElement;
		play(chat, { motion: true });
		expect(chat.querySelectorAll(".w").length).toBe(3);
		expect(chat.textContent).toBe("One two  three");
	});
});
