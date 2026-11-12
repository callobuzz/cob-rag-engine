import { createContext, type ReactNode, useCallback, useContext, useMemo, useState } from "react";
import type { ApiError } from "@/lib/api/errors";

/**
 * Small state the Assistant tab shares between its bar and its sections:
 *  - whether the section form on screen has unsaved edits (publishing sends the saved draft, so
 *    Publish waits: an unsaved edit would silently not go live);
 *  - the last publish error, so the Leads section can say "leads need an encryption key" next to
 *    the switch that caused it.
 */
interface SharedValue {
	dirty: boolean;
	setDirty: (dirty: boolean) => void;
	publishError: ApiError | null;
	setPublishError: (e: ApiError | null) => void;
}

const NOOP: SharedValue = { dirty: false, setDirty: () => {}, publishError: null, setPublishError: () => {} };
const Ctx = createContext<SharedValue>(NOOP);

export function AssistantDirtyProvider({ children }: { children: ReactNode }) {
	const [dirty, setDirtyState] = useState(false);
	const [publishError, setPublishErrorState] = useState<ApiError | null>(null);
	const setDirty = useCallback((d: boolean) => setDirtyState(d), []);
	const setPublishError = useCallback((e: ApiError | null) => setPublishErrorState(e), []);
	const value = useMemo(
		() => ({ dirty, setDirty, publishError, setPublishError }),
		[dirty, setDirty, publishError, setPublishError],
	);
	return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAssistantDirty(): SharedValue {
	return useContext(Ctx);
}
