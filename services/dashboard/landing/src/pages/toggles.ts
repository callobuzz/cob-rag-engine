import { chooseIndex, type Option } from "./select";

/**
 * The currency and billing-period toggles. They are plain links (`?currency=USD&period=yearly`)
 * that work without JavaScript; here they swap the visible variant of every plan card without a
 * reload and remember the choice. A blocked or full storage is fine: nothing here needs it.
 */
export const STORAGE_KEY = "landing.pricing";

export type Choice = { currency: string; period: string };
type Store = Pick<Storage, "getItem" | "setItem">;

export function readChoice(store: Store | null): Choice | null {
	try {
		const raw = store?.getItem(STORAGE_KEY);
		if (!raw) return null;
		const parsed = JSON.parse(raw) as Partial<Choice>;
		if (typeof parsed.currency === "string" && typeof parsed.period === "string") {
			return { currency: parsed.currency, period: parsed.period };
		}
	} catch {
		return null;
	}
	return null;
}

export function saveChoice(store: Store | null, choice: Choice): void {
	try {
		store?.setItem(STORAGE_KEY, JSON.stringify(choice));
	} catch {
		// storage blocked or full: the choice simply is not remembered
	}
}

export function applyChoice(root: HTMLElement, choice: Choice): void {
	root.dataset.currency = choice.currency;
	root.dataset.period = choice.period;
	for (const card of root.querySelectorAll<HTMLElement>("[data-card]")) {
		const variants = [...card.querySelectorAll<HTMLElement>("[data-variant]")];
		const options: Option[] = variants.map((v) => [v.dataset.currency ?? "", v.dataset.period ?? ""]);
		const at = chooseIndex(options, choice.currency, choice.period);
		variants.forEach((variant, i) => {
			variant.hidden = i !== at;
		});
	}
	for (const link of root.querySelectorAll<HTMLAnchorElement>("[data-toggle]")) {
		const kind = link.dataset.toggle;
		const active = kind === "currency" ? link.dataset.value === choice.currency : link.dataset.value === choice.period;
		if (active) link.setAttribute("aria-current", "true");
		else link.removeAttribute("aria-current");
	}
	root.dispatchEvent(new CustomEvent("pricing:change", { detail: choice }));
}

function available(root: HTMLElement, kind: string, value: string): boolean {
	return [...root.querySelectorAll<HTMLElement>(`[data-toggle="${kind}"]`)].some((l) => l.dataset.value === value);
}

export function initToggles(doc: Document = document, win: Window = window): void {
	const root = doc.querySelector<HTMLElement>("[data-pricing]");
	if (!root) return;
	let store: Store | null = null;
	try {
		store = win.localStorage;
	} catch {
		store = null;
	}
	const current = (): Choice => ({
		currency: root.dataset.currency ?? "",
		period: root.dataset.period ?? "",
	});
	root.addEventListener("click", (event) => {
		const link = (event.target as Element | null)?.closest<HTMLAnchorElement>("[data-toggle]");
		if (!link || !root.contains(link)) return;
		event.preventDefault();
		const next = current();
		if (link.dataset.toggle === "currency") next.currency = link.dataset.value ?? next.currency;
		else next.period = link.dataset.value ?? next.period;
		applyChoice(root, next);
		saveChoice(store, next);
	});
	// A link with a choice in it is the visitor's word; otherwise use what they chose last time.
	if (/[?&](currency|period)=/.test(win.location.search)) return;
	const saved = readChoice(store);
	if (!saved) return;
	const before = current();
	const next = { ...before };
	if (available(root, "currency", saved.currency)) next.currency = saved.currency;
	if (available(root, "period", saved.period)) next.period = saved.period;
	if (next.currency !== before.currency || next.period !== before.period) applyChoice(root, next);
}
