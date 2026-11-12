import { startReveal } from "./reveal";
import type { Win } from "./types";

/**
 * Motion is opted into, never assumed. The page ships with everything visible; a 200-byte inline
 * check adds `motion-ok` to <html> only where JavaScript runs and the visitor has not asked for
 * less motion. Without that class nothing here does anything.
 *
 * - "off": no `motion-ok` (reduced motion, or the check did not run): the static story.
 * - "native": the browser has scroll-driven animations; CSS does all the work.
 * - "fallback": a small IntersectionObserver adds `.is-in` to `[data-reveal]` elements.
 */
export type MotionMode = "off" | "native" | "fallback";

type CssLike = { supports?: (property: string) => boolean } | undefined;

export function motionMode(doc: Document, css: CssLike): MotionMode {
	if (!doc.documentElement.classList.contains("motion-ok")) return "off";
	if (css?.supports?.("animation-timeline: view()")) return "native";
	return "fallback";
}

export function initMotion(doc: Document = document, win: Win = window): MotionMode {
	const css = (win as { CSS?: CssLike }).CSS;
	const mode = motionMode(doc, css);
	if (mode === "fallback") startReveal(doc, win);
	return mode;
}
