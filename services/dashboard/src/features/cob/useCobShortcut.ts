import { useEffect } from "react";

/** True for Ctrl+/ (Windows, Linux) and Cmd+/ (macOS). */
export function isCobShortcut(e: Pick<KeyboardEvent, "key" | "code" | "ctrlKey" | "metaKey" | "altKey" | "shiftKey">) {
	return (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && (e.key === "/" || e.code === "Slash");
}

/**
 * Ctrl+/ (Cmd+/) toggles COB from anywhere, including inside text fields: the chord never types a
 * character, so it is safe to take over (the palette keeps Ctrl+K).
 */
export function useCobShortcut(toggle: () => void): void {
	useEffect(() => {
		const onKey = (e: KeyboardEvent) => {
			if (e.defaultPrevented || !isCobShortcut(e)) return;
			e.preventDefault();
			toggle();
		};
		document.addEventListener("keydown", onKey);
		return () => document.removeEventListener("keydown", onKey);
	}, [toggle]);
}

export function isMac(): boolean {
	return typeof navigator !== "undefined" && /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent);
}
