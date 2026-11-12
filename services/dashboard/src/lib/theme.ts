import { useSyncExternalStore } from "react";
import { readPref, writePref } from "@/lib/auth/storage";

/**
 * Theme light / dark / system (localStorage["cob.theme"], class "dark" on <html>) and table
 * density (localStorage["cob.density"], data-density on <html>). public/theme.js applies both
 * before first paint; this module keeps them in sync afterwards.
 */
export type ThemeChoice = "light" | "dark" | "system";
export type Density = "comfortable" | "compact";

const listeners = new Set<() => void>();
const emit = () => {
	for (const l of listeners) l();
};

function subscribe(l: () => void) {
	listeners.add(l);
	return () => listeners.delete(l);
}

export function getTheme(): ThemeChoice {
	const t = readPref("cob.theme");
	return t === "light" || t === "dark" ? t : "system";
}

function systemDark(): boolean {
	return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-color-scheme: dark)").matches;
}

export function resolvedTheme(choice: ThemeChoice = getTheme()): "light" | "dark" {
	return choice === "system" ? (systemDark() ? "dark" : "light") : choice;
}

export function applyTheme(choice: ThemeChoice = getTheme()): void {
	const root = document.documentElement;
	root.classList.toggle("dark", resolvedTheme(choice) === "dark");
	root.setAttribute("data-theme-choice", choice);
}

export function setTheme(choice: ThemeChoice): void {
	writePref("cob.theme", choice);
	applyTheme(choice);
	emit();
}

export function getDensity(): Density {
	return readPref("cob.density") === "compact" ? "compact" : "comfortable";
}

export function setDensity(d: Density): void {
	writePref("cob.density", d);
	document.documentElement.setAttribute("data-density", d);
	emit();
}

/** Follow the OS when the choice is "system". Returns a cleanup. */
export function watchSystemTheme(): () => void {
	const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
	if (!mq) return () => {};
	const on = () => {
		if (getTheme() === "system") {
			applyTheme("system");
			emit();
		}
	};
	mq.addEventListener("change", on);
	return () => mq.removeEventListener("change", on);
}

export function useTheme(): ThemeChoice {
	return useSyncExternalStore(subscribe, getTheme, getTheme);
}

export function useResolvedTheme(): "light" | "dark" {
	return useSyncExternalStore(
		subscribe,
		() => resolvedTheme(),
		() => "light",
	);
}

export function useDensity(): Density {
	return useSyncExternalStore(subscribe, getDensity, getDensity);
}
