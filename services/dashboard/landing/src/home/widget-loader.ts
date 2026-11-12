/**
 * "Ask it yourself": the deployer's real chat widget, loaded only when the visitor clicks.
 *
 * Nothing is fetched before the click, so the page costs no model call and no extra request on
 * load. The first click adds one <script src="/widget.js"> with the page's public key, opens the
 * panel and puts the hero's first question in the message box (the widget never sends it: the
 * visitor sends their own). Later clicks only open the panel again.
 */
export const WIDGET_SRC = "/widget.js";
const MAX_ASK = 200;

type WidgetWindow = Window & { __cobRagWidgetOpen?: () => void };

export function loadWidget(button: HTMLElement, doc: Document = document): void {
	const win = doc.defaultView as WidgetWindow | null;
	if (doc.querySelector(`script[src="${WIDGET_SRC}"]`)) {
		win?.__cobRagWidgetOpen?.();
		return;
	}
	const key = button.getAttribute("data-ask-key");
	if (!key) return;
	const script = doc.createElement("script");
	script.src = WIDGET_SRC;
	script.async = true;
	script.setAttribute("data-key", key);
	script.setAttribute("data-open", "true");
	const ask = (button.getAttribute("data-ask-text") ?? "").slice(0, MAX_ASK);
	if (ask) script.setAttribute("data-ask", ask);
	doc.body.append(script);
}

export function initWidgetLoader(doc: Document = document): void {
	for (const button of doc.querySelectorAll<HTMLElement>("[data-ask]")) {
		button.addEventListener("click", () => loadWidget(button, doc));
	}
}
