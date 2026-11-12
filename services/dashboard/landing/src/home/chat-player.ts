/**
 * The hero conversation player.
 *
 * The page ships with every message at its final state. Where motion is allowed this module
 * hides them again and re-plays them in order: the visitor's line wipes in, the assistant shows
 * typing dots and then its words arrive one by one, the source chip and the lead card follow.
 * The whole thing takes at most four seconds, never changes a word of text and finishes at
 * once on any scroll, key press or tap.
 */
import type { Win } from "../core/types";

export const MAX_MS = 3800;
const VISITOR_MS_PER_CHAR = 18;
const VISITOR_MAX_MS = 1200;
const TYPING_MS = 700;
const WORD_MS = 45;
const SETTLE_MS = 280;
const PAUSE_MS = 220;

export interface PlayOptions {
	motion: boolean;
	win?: Win;
}

type Step = { at: number; run: () => void };

function words(paragraph: HTMLElement): number {
	const text = paragraph.firstChild;
	if (text?.nodeType !== 3 || !text.textContent) return 0;
	const parts = text.textContent.split(/(\s+)/);
	const doc = paragraph.ownerDocument;
	const fragment = doc.createDocumentFragment();
	let count = 0;
	for (const part of parts) {
		if (part === "" || /^\s+$/.test(part)) {
			fragment.append(doc.createTextNode(part));
			continue;
		}
		const span = doc.createElement("span");
		span.className = "w";
		span.style.setProperty("--k", String(count));
		span.textContent = part;
		fragment.append(span);
		count += 1;
	}
	paragraph.replaceChild(fragment, text);
	return count;
}

function plan(items: HTMLElement[]): { steps: Step[]; total: number } {
	const steps: Step[] = [];
	let t = 0;
	for (const item of items) {
		if (item.hasAttribute("data-lead")) {
			const at = t;
			steps.push({ at, run: () => item.classList.remove("is-pending") });
			t += SETTLE_MS;
			continue;
		}
		const assistant = item.getAttribute("data-role") === "assistant";
		if (assistant) {
			const paragraph = item.querySelector<HTMLElement>("p");
			const count = paragraph ? words(paragraph) : 0;
			const typingAt = t;
			steps.push({
				at: typingAt,
				run: () => {
					item.classList.remove("is-pending");
					item.classList.add("is-typing");
				},
			});
			t += TYPING_MS;
			const showAt = t;
			steps.push({
				at: showAt,
				run: () => {
					item.classList.remove("is-typing");
					item.classList.add("is-words");
				},
			});
			t += count * WORD_MS + SETTLE_MS;
		} else {
			const length = (item.textContent ?? "").trim().length;
			const wipe = Math.min(VISITOR_MAX_MS, length * VISITOR_MS_PER_CHAR);
			const at = t;
			steps.push({
				at,
				run: () => {
					item.style.setProperty("--wipe", `${wipe}ms`);
					item.classList.remove("is-pending");
					item.classList.add("is-wiping");
				},
			});
			t += wipe + PAUSE_MS;
		}
	}
	return { steps, total: t };
}

/** Plays one chat. Returns the function that finishes it at once. */
export function play(chat: HTMLElement, options: PlayOptions): () => void {
	const win = options.win ?? (window as Win);
	if (!options.motion) return () => {};
	const items = Array.from(chat.querySelectorAll<HTMLElement>(".msg, [data-lead]"));
	if (items.length === 0) return () => {};

	for (const item of items) item.classList.add("is-pending");
	const { steps, total } = plan(items);
	const scale = total > MAX_MS ? MAX_MS / total : 1;

	const timers: number[] = [];
	let done = false;
	const events = ["scroll", "keydown", "pointerdown", "wheel", "touchstart"];

	const finish = (): void => {
		if (done) return;
		done = true;
		for (const timer of timers) win.clearTimeout(timer);
		for (const name of events) win.removeEventListener(name, finish);
		for (const item of items) {
			item.classList.remove("is-pending", "is-typing");
			item.classList.add("is-done");
		}
	};

	for (const name of events) win.addEventListener(name, finish, { passive: true });
	for (const step of steps) {
		timers.push(win.setTimeout(step.run, Math.floor(step.at * scale)));
	}
	timers.push(win.setTimeout(finish, Math.ceil(total * scale) + SETTLE_MS));
	return finish;
}
