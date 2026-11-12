/**
 * The sticky header: a shadow once the page has scrolled, and a menu that is a <details> on a
 * phone (works without JavaScript) and always open from the wide breakpoint.
 */
export const WIDE_QUERY = "(min-width: 880px)";
const SCROLLED_AFTER = 8;

export function initHeader(doc: Document = document, win: Window = window): void {
	const header = doc.querySelector<HTMLElement>("[data-header]");
	if (header) {
		let waiting = false;
		const update = (): void => {
			waiting = false;
			header.toggleAttribute("data-scrolled", win.scrollY > SCROLLED_AFTER);
		};
		win.addEventListener(
			"scroll",
			() => {
				if (waiting) return;
				waiting = true;
				win.requestAnimationFrame(update);
			},
			{ passive: true },
		);
		update();
	}

	const menu = doc.querySelector<HTMLDetailsElement>("[data-menu]");
	if (!menu) return;
	const wide = typeof win.matchMedia === "function" ? win.matchMedia(WIDE_QUERY) : null;
	const sync = (): void => {
		if (wide?.matches) menu.open = true;
	};
	sync();
	wide?.addEventListener?.("change", () => {
		menu.open = wide.matches;
	});
	menu.addEventListener("click", (event) => {
		if (!wide?.matches && event.target instanceof HTMLAnchorElement) menu.open = false;
	});
	doc.addEventListener("keydown", (event) => {
		if (event.key === "Escape" && !wide?.matches && menu.open) {
			menu.open = false;
			menu.querySelector("summary")?.focus();
		}
	});
}
