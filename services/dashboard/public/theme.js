// No-flash theme: runs before the app bundle and before first paint. A file (not an inline
// script) so the D12 Content-Security-Policy needs no 'unsafe-inline'.
// Reads localStorage["cob.theme"] ("light" | "dark" | "system", default "system") and
// localStorage["cob.density"] ("comfortable" | "compact").
(() => {
	const root = document.documentElement;
	let theme = "system";
	let density = "comfortable";
	try {
		const t = window.localStorage.getItem("cob.theme");
		if (t === "light" || t === "dark" || t === "system") theme = t;
		if (window.localStorage.getItem("cob.density") === "compact") density = "compact";
	} catch (_e) {
		// Storage blocked (private mode): keep the defaults.
	}
	const dark =
		theme === "dark" ||
		(theme === "system" && !!window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
	root.classList.toggle("dark", dark);
	root.setAttribute("data-theme-choice", theme);
	root.setAttribute("data-density", density);
})();
