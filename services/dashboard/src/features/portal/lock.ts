import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { lockHolder } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { usePrincipal } from "@/lib/auth/store";
import { actorFields, actorName, pk, portal, type Workspace } from "./api";
import { toastPortalError } from "./messages";

/** Workspaces in these states can't be locked or edited any more. */
export const CLOSED_STATUSES = new Set(["ingested", "archived"]);

/** The engine lets a lock go stale after RAG_PORTAL_LOCK_TIMEOUT_MINUTES (30); refresh well before. */
const HEARTBEAT_MS = 10 * 60_000;

export interface LockState {
	/** The signed-in principal holds the lock: chunk edits are allowed. */
	mine: boolean;
	/** Someone else holds it (from the workspace, or from a 423 on "Start editing"). */
	holder: string | null;
	lockedAt: string | null;
	closed: boolean;
	start: () => void;
	stop: () => void;
	pending: boolean;
	/** Call after a write was refused because the lock is gone: reloads the workspace. */
	lost: () => void;
}

/**
 * The portal's edit lock (D11 "Editing lock"): "Start editing" → `POST …/lock`; released with
 * "Stop editing", when leaving the page (unmount) and on `pagehide` (a best-effort
 * `fetch(…, {keepalive: true})`, since `navigator.sendBeacon` can't send the auth header);
 * refreshed every 10 minutes while held; a 423 means someone else holds it → read-only.
 */
export function useWorkspaceLock(w: Workspace): LockState {
	const principal = usePrincipal();
	const qc = useQueryClient();
	const me = actorName(principal);
	const actor = actorFields(principal);
	const [refusedBy, setRefusedBy] = useState<string | null>(null);
	const mine = !!w.locked_by && w.locked_by === me;
	const closed = CLOSED_STATUSES.has(w.status);

	const held = useRef(false);
	const actorRef = useRef(actor);
	held.current = mine && !closed;
	actorRef.current = actor;

	const patch = (data: Pick<Workspace, "locked_by" | "locked_at">) =>
		qc.setQueryData<Workspace>(pk.detail(w.id), (prev) =>
			prev ? { ...prev, locked_by: data.locked_by, locked_at: data.locked_at } : prev,
		);

	const lock = useMutation({
		mutationFn: () => portal.lock(w.id, actorRef.current),
		onSuccess: (data) => {
			setRefusedBy(null);
			patch(data);
		},
		onError: (e) => {
			const err = toApiError(e);
			if (err.status === 423) {
				setRefusedBy(lockHolder(err.detail) ?? "");
				void qc.invalidateQueries({ queryKey: pk.detail(w.id) });
				return;
			}
			toastPortalError(e, "lock");
		},
	});
	const unlock = useMutation({
		mutationFn: () => portal.unlock(w.id, actorRef.current),
		onSuccess: (data) => patch(data),
		onError: (e) => {
			toastPortalError(e, "lock");
			void qc.invalidateQueries({ queryKey: pk.detail(w.id) });
		},
	});

	// Release on leave (unmount or closing the tab), if still held.
	useEffect(() => {
		const id = w.id;
		const release = () => {
			if (held.current) {
				held.current = false;
				portal.unlockOnLeave(id, actorRef.current);
			}
		};
		window.addEventListener("pagehide", release);
		return () => {
			window.removeEventListener("pagehide", release);
			release();
		};
	}, [w.id]);

	// Keep a held lock fresh (the engine lets it go stale after 30 minutes).
	const heldNow = mine && !closed;
	useEffect(() => {
		if (!heldNow) return;
		const id = w.id;
		const timer = window.setInterval(() => {
			void portal.lock(id, actorRef.current).catch(() => undefined);
		}, HEARTBEAT_MS);
		return () => window.clearInterval(timer);
	}, [heldNow, w.id]);

	const otherHolder = w.locked_by && !mine ? w.locked_by : refusedBy || null;
	return {
		mine: mine && !closed,
		holder: mine ? null : otherHolder,
		lockedAt: w.locked_at ?? null,
		closed,
		start: () => lock.mutate(),
		stop: () => unlock.mutate(),
		pending: lock.isPending || unlock.isPending,
		lost: () => {
			void qc.invalidateQueries({ queryKey: pk.detail(w.id) });
		},
	};
}
