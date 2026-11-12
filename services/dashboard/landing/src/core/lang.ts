/**
 * The language switch. The links are rendered by the server for the page being viewed (the switch
 * keeps the path), so this only remembers the visitor's choice and, on the home page of the
 * default language, offers the remembered language once. Search engines carry no storage, so they
 * always see the default.
 */
export const LANG_KEY = "cob.landing.lang";

export type LangLink = { lang: string; href: string };

export function pickRedirect(
	currentLang: string,
	stored: string | null,
	links: readonly LangLink[],
	path: string,
): string | null {
	if (!stored || stored === currentLang || path !== "/") return null;
	return links.find((link) => link.lang === stored)?.href ?? null;
}

function read(win: Window): string | null {
	try {
		return win.localStorage.getItem(LANG_KEY);
	} catch {
		return null;
	}
}

function write(win: Window, value: string): void {
	try {
		win.localStorage.setItem(LANG_KEY, value);
	} catch {
		// Private windows and blocked storage: the switch still works, it just forgets.
	}
}

export function initLang(doc: Document = document, win: Window = window): void {
	const switcher = doc.querySelector<HTMLElement>("[data-lang-switch]");
	if (!switcher) return;
	const anchors = Array.from(switcher.querySelectorAll<HTMLAnchorElement>("a[hreflang]"));
	const links = anchors.map((a) => ({ lang: a.getAttribute("hreflang") ?? "", href: a.getAttribute("href") ?? "" }));
	for (const anchor of anchors) {
		anchor.addEventListener("click", () => write(win, anchor.getAttribute("hreflang") ?? ""));
	}
	const target = pickRedirect(doc.documentElement.lang, read(win), links, win.location.pathname);
	const arrivedHere = doc.referrer.startsWith(win.location.origin);
	if (target && !arrivedHere) win.location.replace(target);
}
