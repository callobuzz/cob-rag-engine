import { type KeyboardEvent, type PointerEvent, useCallback, useEffect, useRef, useState } from "react";
import { cobStrings } from "@/lib/strings/cob";

export const COB_MIN_WIDTH = 360;
export const COB_MAX_WIDTH = 720;
export const COB_DEFAULT_WIDTH = 420;
export const COB_WIDTH_KEY = "cob:width";
const STEP = 20;

/** The sidebar (248 px) plus the least the page keeps beside the docked panel. */
export const COB_RESERVED = 248 + 360;

/** The widest the panel may be in a window this wide: the page keeps room, never below the minimum. */
export const maxWidthFor = (viewport: number): number =>
	Math.max(COB_MIN_WIDTH, Math.min(COB_MAX_WIDTH, viewport - COB_RESERVED));

const viewportWidth = (): number => (typeof window === "undefined" ? Number.POSITIVE_INFINITY : window.innerWidth);

export const clampWidth = (w: number, viewport: number = viewportWidth()): number =>
	Math.min(maxWidthFor(viewport), Math.max(COB_MIN_WIDTH, Math.round(w)));

function readWidth(): number {
	try {
		const raw = window.localStorage.getItem(COB_WIDTH_KEY);
		const n = raw ? Number(raw) : Number.NaN;
		return Number.isFinite(n) ? clampWidth(n) : COB_DEFAULT_WIDTH;
	} catch {
		return COB_DEFAULT_WIDTH;
	}
}

function writeWidth(w: number): void {
	try {
		window.localStorage.setItem(COB_WIDTH_KEY, String(w));
	} catch {
		// Storage blocked: the width lasts for this page only.
	}
}

/**
 * The docked panel's width (360 to 720 px), kept in localStorage["cob:width"]. The handle is a
 * vertical `separator`: drag with a pointer, or focus it and press the arrow keys (left widens,
 * since the panel is on the right) in 20 px steps; Home and End jump to the limits.
 */
export function useResizableWidth() {
	const [width, setWidthState] = useState(readWidth);
	const drag = useRef<{ x: number; w: number } | null>(null);
	const [max, setMax] = useState(() => maxWidthFor(viewportWidth()));

	// A narrower window shrinks the panel (the saved width is kept for when the window grows again).
	useEffect(() => {
		const onResize = () => setMax(maxWidthFor(window.innerWidth));
		window.addEventListener("resize", onResize);
		return () => window.removeEventListener("resize", onResize);
	}, []);
	const effective = Math.min(width, max);

	const setWidth = useCallback((w: number) => {
		const next = clampWidth(w);
		setWidthState(next);
		writeWidth(next);
	}, []);

	/** End a drag (release, cancel or lost capture): keep the width reached, or restore it. */
	const finish = (e: PointerEvent<HTMLElement>) => {
		const d = drag.current;
		drag.current = null;
		if (d) setWidth(d.w + (d.x - e.clientX));
	};

	const handleProps = {
		role: "separator" as const,
		"aria-orientation": "vertical" as const,
		"aria-label": cobStrings.resize,
		"aria-valuenow": effective,
		"aria-valuemin": COB_MIN_WIDTH,
		"aria-valuemax": max,
		tabIndex: 0,
		onPointerDown: (e: PointerEvent<HTMLElement>) => {
			drag.current = { x: e.clientX, w: effective };
			e.currentTarget.setPointerCapture?.(e.pointerId);
		},
		onPointerMove: (e: PointerEvent<HTMLElement>) => {
			const d = drag.current;
			if (!d) return;
			if (e.buttons === 0) {
				drag.current = null;
				return;
			}
			setWidthState(clampWidth(d.w + (d.x - e.clientX)));
		},
		onPointerCancel: finish,
		onLostPointerCapture: finish,
		onPointerUp: (e: PointerEvent<HTMLElement>) => {
			e.currentTarget.releasePointerCapture?.(e.pointerId);
			finish(e);
		},
		onKeyDown: (e: KeyboardEvent<HTMLElement>) => {
			const next =
				e.key === "ArrowLeft"
					? effective + STEP
					: e.key === "ArrowRight"
						? effective - STEP
						: e.key === "Home"
							? COB_MIN_WIDTH
							: e.key === "End"
								? max
								: null;
			if (next === null) return;
			e.preventDefault();
			setWidth(next);
		},
	};

	return { width: effective, setWidth, handleProps };
}
