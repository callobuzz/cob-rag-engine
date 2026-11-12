import { useCallback, useEffect, useRef, useState } from "react";
import { messageFor } from "@/lib/api/error-messages";
import { ApiError, parseApiError, toApiError } from "@/lib/api/errors";
import { readSse } from "@/lib/api/sse";
import { currentRagUrl } from "@/lib/auth/store";
import { cobPublicStrings as t } from "@/lib/strings/cob-public";
import { fill } from "@/lib/strings/en";
import { initialTurn, reduceTurn } from "../cob/stream";
import type { CobTurnView } from "../cob/types";

/**
 * The public COB's data layer: `POST /api/v1/public/cob/chat`, no credential. Everything lives in
 * this hook's memory: nothing goes to localStorage or sessionStorage, and a reload starts afresh.
 * The next question carries the earlier ones of this page view (at most six messages) because the
 * engine keeps no chat for visitors.
 */
export const PUBLIC_MESSAGE_MAX = 1000;
export const HISTORY_MAX = 6;
export const HISTORY_ITEM_MAX = 2000;

export interface HistoryItem {
	role: "user" | "assistant";
	content: string;
}

/** The last answered questions as the engine's `history`: pairs, newest last, at most six messages. */
export function historyFrom(turns: CobTurnView[]): HistoryItem[] {
	const items: HistoryItem[] = [];
	for (const turn of turns) {
		if (turn.status !== "done" || !turn.text) continue;
		items.push(
			{ role: "user", content: turn.question.slice(0, HISTORY_ITEM_MAX) },
			{ role: "assistant", content: turn.text.slice(0, HISTORY_ITEM_MAX) },
		);
	}
	return items.slice(-HISTORY_MAX);
}

export interface PublicCob {
	turns: CobTurnView[];
	busy: boolean;
	send: (message: string) => Promise<void>;
	stop: () => void;
	reset: () => void;
	retry: () => void;
	/** Set by a 429: when the wait ends (ms timestamp) and which limit it was. */
	limit: { until: number; scope: string } | null;
}

const errorEvent = (detail: string, code?: string | null) => ({
	event: "error",
	data: JSON.stringify({ detail, ...(code ? { code } : {}) }),
});

export function usePublicCob(): PublicCob {
	const [turns, setTurns] = useState<CobTurnView[]>([]);
	const [busy, setBusy] = useState(false);
	const [limit, setLimit] = useState<PublicCob["limit"]>(null);
	const turnsRef = useRef(turns);
	turnsRef.current = turns;
	const abort = useRef<AbortController | null>(null);
	const alive = useRef(true);

	useEffect(() => {
		alive.current = true;
		return () => {
			alive.current = false;
			abort.current?.abort();
		};
	}, []);

	const patchLast = useCallback((fn: (t: CobTurnView) => CobTurnView) => {
		if (!alive.current) return;
		setTurns((all) => (all.length ? [...all.slice(0, -1), fn(all[all.length - 1] as CobTurnView)] : all));
	}, []);

	const send = useCallback(
		async (message: string) => {
			const text = message.trim();
			if (!text || abort.current) return;
			if (text.length > PUBLIC_MESSAGE_MAX) {
				setTurns((all) => [
					...all,
					reduceTurn(
						{ ...initialTurn(text.slice(0, 80)), live: true },
						errorEvent(fill(t.tooLong, { max: PUBLIC_MESSAGE_MAX })),
					),
				]);
				return;
			}
			setLimit(null);
			const history = historyFrom(turnsRef.current);
			const controller = new AbortController();
			abort.current = controller;
			setBusy(true);
			setTurns((all) => [...all, { ...initialTurn(text), live: true }]);
			try {
				const response = await fetch(`${currentRagUrl()}/api/v1/public/cob/chat`, {
					method: "POST",
					headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
					credentials: "omit",
					body: JSON.stringify({ message: text, ...(history.length ? { history } : {}) }),
					signal: controller.signal,
				});
				if (!response.ok) throw await parseApiError(response);
				if (!response.body) throw new ApiError({ status: 0, code: "network", detail: "The reply stream was empty." });
				let ended = false;
				for await (const ev of readSse(response.body, controller.signal)) {
					patchLast((turn) => reduceTurn(turn, ev));
					if (ev.event === "done" || ev.event === "error") ended = true;
				}
				if (!ended && !controller.signal.aborted) patchLast((turn) => reduceTurn(turn, errorEvent("", "network")));
			} catch (e) {
				const err = toApiError(e);
				if (err.code === "cob_public_rate_limited" && err.retryAfterS != null && err.retryAfterS > 0 && alive.current) {
					const scope = typeof err.extra.scope === "string" ? err.extra.scope : "ip";
					setLimit({ until: Date.now() + err.retryAfterS * 1000, scope });
				}
				if (err.code !== "aborted") {
					patchLast((turn) => reduceTurn(turn, errorEvent(messageFor(err).title, err.code)));
				}
			} finally {
				if (abort.current === controller) abort.current = null;
				if (alive.current) setBusy(false);
				patchLast((turn) => (turn.status === "streaming" ? { ...turn, status: "done", stage: null } : turn));
			}
		},
		[patchLast],
	);

	const stop = useCallback(() => abort.current?.abort(), []);

	const reset = useCallback(() => {
		abort.current?.abort();
		abort.current = null;
		setBusy(false);
		setLimit(null);
		setTurns([]);
	}, []);

	const retry = useCallback(() => {
		const last = turnsRef.current[turnsRef.current.length - 1];
		if (last?.status !== "error" || abort.current) return;
		setTurns((all) => all.slice(0, -1));
		void send(last.question);
	}, [send]);

	return { turns, busy, send, stop, reset, retry, limit };
}
