/**
 * The seam between the dashboard and COB's panel: anything on a screen can ask COB to open
 * ("Ask COB about this page", the "?" help link, a starter) without importing the panel.
 * A module-level listener set, no React. The panel (features/cob) subscribes; callers publish.
 */
export interface CobOpenRequest {
	/** Text to put in the panel's input. */
	prompt?: string;
	/** A Docs page slug COB should treat as the page being read. */
	docsSlug?: string;
	/** Send the prompt straight away instead of leaving it in the input. */
	send?: boolean;
}

type Listener = (req: CobOpenRequest) => void;
const listeners = new Set<Listener>();

export function openCob(req: CobOpenRequest = {}): void {
	for (const fn of [...listeners]) fn(req);
}

/** Subscribe to open requests; returns the unsubscribe function. */
export function onCobOpen(fn: Listener): () => void {
	listeners.add(fn);
	return () => {
		listeners.delete(fn);
	};
}
