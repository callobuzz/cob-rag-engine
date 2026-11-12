// The widget preview page's loader (dashboard D6/D12), a file rather than an inline script so the
// page's Content-Security-Policy needs no 'unsafe-inline'.
// ?key=pk_...&api=http://127.0.0.1:8000 (the dashboard's Preview fills these in).
(() => {
	const trimSlashes = (value) => {
		let out = String(value);
		while (out.endsWith("/")) out = out.slice(0, -1);
		return out;
	};
	// This page shares the dashboard's origin, so it must never load a script from an address
	// named only in the link: the api parameter is used only when it is this dashboard's engine (config.js,
	// or the engine a key sign-in chose, stored by the dashboard as cob.key).
	const knownEngines = () => {
		const cfg = globalThis.RAG_CONFIG || {};
		const out = [trimSlashes(cfg.ragUrl || "http://127.0.0.1:8000")];
		for (const area of ["sessionStorage", "localStorage"]) {
			try {
				const stored = JSON.parse(window[area].getItem("cob.key") || "null");
				if (stored && typeof stored.ragUrl === "string") out.push(trimSlashes(stored.ragUrl));
			} catch {
				// storage blocked or not JSON
			}
		}
		return out;
	};
	document.getElementById("origin").textContent = location.origin;
	const params = new URLSearchParams(location.search);
	const key = params.get("key");
	const engines = knownEngines();
	const asked = trimSlashes(params.get("api") || "");
	const apiBase = asked && engines.includes(asked) ? asked : engines[0];
	if (!key) {
		const box = document.createElement("div");
		box.className = "missing";
		box.setAttribute("role", "status");
		box.innerHTML =
			"No widget key given. Open this page from the dashboard's <b>Website widget → Preview</b>, or add <code>?key=pk_...</code> to the address.";
		document.querySelector("main").prepend(box);
		return;
	}
	const s = document.createElement("script");
	s.src = apiBase.concat("/widget.js");
	s.async = true;
	s.setAttribute("data-key", key);
	s.setAttribute("data-api", apiBase);
	s.setAttribute("data-open", "true");
	document.body.appendChild(s);
})();
