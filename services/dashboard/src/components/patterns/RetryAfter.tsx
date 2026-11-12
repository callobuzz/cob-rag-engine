import { useEffect, useState } from "react";
import { formatClock, formatDuration } from "@/lib/api/error-messages";

/** Seconds left until `until` (ms timestamp), ticking every second; 0 when passed. */
export function useCountdown(until: number | null): number {
	const [now, setNow] = useState(() => Date.now());
	useEffect(() => {
		if (!until) return;
		setNow(Date.now());
		const t = setInterval(() => setNow(Date.now()), 1000);
		return () => clearInterval(t);
	}, [until]);
	if (!until) return 0;
	return Math.max(0, Math.ceil((until - now) / 1000));
}

/**
 * Countdown text. The visible text ticks every second; the screen-reader copy is updated at most
 * every 10 s (D1 accessibility: aria-live polite, not chatty).
 */
export function RetryAfter({
	until,
	format = "words",
	template = "{t}",
}: {
	until: number | null;
	format?: "words" | "clock";
	template?: string;
}) {
	const left = useCountdown(until);
	const text = format === "clock" ? formatClock(left) : formatDuration(left);
	const announced =
		format === "clock" ? formatClock(Math.ceil(left / 10) * 10) : formatDuration(Math.ceil(left / 10) * 10);
	return (
		<>
			<span aria-hidden>{template.replace("{t}", text)}</span>
			<span className="sr-only" aria-live="polite">
				{left > 0 ? template.replace("{t}", announced) : ""}
			</span>
		</>
	);
}
