import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { LogoArt } from "./LogoArt";
import { useLogoIds } from "./LogoMark";
import "./logo.css";

type LogoState = "armed" | "assemble" | "live";

/** How long the assemble intro runs before the calm loop takes over (matches logo.css). */
const ASSEMBLE_MS = 3100;

function prefersReducedMotion(): boolean {
	return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

/**
 * The large animated chrome logo. armed (held) -> assemble (plays once, when first on screen) -> live
 * (calm loop). Pauses while off-screen or the tab is hidden; with reduced motion, or without
 * IntersectionObserver, it stays the static logo. Decorative, so aria-hidden.
 */
export function AnimatedLogo({ className }: { className?: string }) {
	const p = useLogoIds();
	const ref = useRef<HTMLElement>(null);
	const [animate] = useState(() => !prefersReducedMotion() && typeof IntersectionObserver !== "undefined");
	const [state, setState] = useState<LogoState>("armed");
	const [paused, setPaused] = useState(false);

	useEffect(() => {
		const el = ref.current;
		if (!animate || !el) return;
		let timer: ReturnType<typeof setTimeout> | null = null;
		let left = ASSEMBLE_MS;
		let startedAt = 0;
		let visible = false;
		let current: LogoState = "armed";
		const set = (s: LogoState) => {
			current = s;
			setState(s);
		};
		const toLive = () => {
			timer = null;
			set("live");
		};
		const sync = () => {
			const running = visible && !document.hidden;
			setPaused(!running);
			if (running) {
				if (current === "armed") set("assemble");
				if (current === "assemble" && !timer) {
					startedAt = Date.now();
					timer = setTimeout(toLive, left);
				}
			} else if (timer) {
				clearTimeout(timer);
				timer = null;
				left = Math.max(0, left - (Date.now() - startedAt));
			}
		};
		const io = new IntersectionObserver(
			(entries) => {
				for (const e of entries) visible = e.isIntersecting;
				sync();
			},
			{ threshold: 0.35 },
		);
		io.observe(el);
		document.addEventListener("visibilitychange", sync);
		return () => {
			io.disconnect();
			document.removeEventListener("visibilitychange", sync);
			if (timer) clearTimeout(timer);
		};
	}, [animate]);

	return (
		<figure
			ref={ref}
			data-testid="cob-logo-animated"
			className={cn("lm lm-anim", paused && "is-paused", className)}
			data-state={animate ? state : undefined}
			aria-hidden="true"
		>
			<svg className="lm-ring lm-gear" viewBox="-24 -24 560 560" aria-hidden="true" focusable="false">
				<circle className="lm-gear-teeth" cx="256" cy="260" r="252" pathLength="360" />
				<circle className="lm-gear-ring" cx="256" cy="260" r="243" />
				<path className="lm-gear-index" d="M256 2 V20 M256 500 V518 M-2 260 H16 M496 260 H514" />
			</svg>
			<svg className="lm-ring lm-scan" viewBox="-24 -24 560 560" aria-hidden="true" focusable="false">
				<circle cx="256" cy="260" r="232" pathLength="100" />
			</svg>
			<svg className="lm-svg" viewBox="-24 -24 560 560" aria-hidden="true" focusable="false">
				<LogoArt p={p} animated />
			</svg>
		</figure>
	);
}
