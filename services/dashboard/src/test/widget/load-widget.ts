// Evaluates the widget's source files the way the engine serves them (`WIDGET_PARTS` in
// api/routes/widget.py): one after another, each seeing the globals the earlier ones set. The
// script is shared with no bundler, so tests run its source. A part that does not exist is skipped.
import fs from "node:fs";
import path from "node:path";

const STATIC = path.resolve(import.meta.dirname, "../../../../../src/callobuzz_rag/widget/static");
const PARTS = ["markdown.js", "widget-core.js", "lead_form.js", "handoff.js", "widget.js"];
const GLOBALS = ["__cobRagWidgetLoaded", "CobMd", "CobWidgetCore", "CobLeadForm", "CobHandoff"];

/** Forget a previous load, then evaluate every part in order. Call after `currentScript` is set. */
export function loadWidgetScripts(): void {
	const w = window as unknown as Record<string, unknown>;
	for (const name of GLOBALS) w[name] = undefined;
	for (const part of PARTS) {
		const file = path.join(STATIC, part);
		if (fs.existsSync(file)) new Function(fs.readFileSync(file, "utf8"))();
	}
}
