import { useCallback, useEffect, useRef, useState } from "react";

/**
 * One polite live region per list, throttled: messages arriving within `ms` are joined and read
 * once, so a list of files moving through stages doesn't flood a screen reader.
 */
export function useAnnouncer(ms = 2000): [(message: string) => void, React.ReactNode] {
	const [text, setText] = useState("");
	const pending = useRef<string[]>([]);
	const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
	const lastFlush = useRef(0);

	const flush = useCallback(() => {
		timer.current = null;
		lastFlush.current = Date.now();
		const next = pending.current.join(". ");
		pending.current = [];
		if (next) setText(next);
	}, []);

	const announce = useCallback(
		(message: string) => {
			pending.current = [...pending.current.filter((m) => m !== message), message];
			if (timer.current) return;
			const wait = Math.max(0, lastFlush.current + ms - Date.now());
			timer.current = setTimeout(flush, wait);
		},
		[flush, ms],
	);

	useEffect(
		() => () => {
			if (timer.current) clearTimeout(timer.current);
		},
		[],
	);

	const node = (
		<div aria-live="polite" role="status" className="sr-only">
			{text}
		</div>
	);
	return [announce, node];
}
