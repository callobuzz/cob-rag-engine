import { useQuery } from "@tanstack/react-query";
import { type Corpus, corporaSwitcherQuery } from "@/lib/api/queries";
import { principalKey } from "@/lib/auth/principal";
import { usePrincipal } from "@/lib/auth/store";
import { useRememberedKb } from "@/lib/kb-selection";

/**
 * The knowledge base the simple pages (Home, Knowledge, Assistant) work on (BIZ3, D13): the one
 * the person last chose, else the business's oldest (the engine's `GET /home` picks the same).
 * A business usually has one; more knowledge bases live under Advanced and the switcher.
 */
export function pickMainKb(kbs: readonly Corpus[], remembered: string | null): Corpus | null {
	const chosen = remembered ? kbs.find((k) => k.id === remembered) : undefined;
	if (chosen) return chosen;
	return [...kbs].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id))[0] ?? null;
}

export interface MainKb {
	kbId: string | null;
	kb: Corpus | null;
	kbs: Corpus[];
	isLoading: boolean;
	isError: boolean;
	error: unknown;
	refetch: () => void;
}

export function useMainKb(): MainKb {
	const principal = usePrincipal();
	const remembered = useRememberedKb(principal ? principalKey(principal) : null);
	const q = useQuery(corporaSwitcherQuery);
	const kbs = q.data?.items ?? [];
	const kb = pickMainKb(kbs, remembered);
	return {
		kbId: kb?.id ?? null,
		kb,
		kbs,
		isLoading: q.isPending,
		isError: q.isError,
		error: q.error,
		refetch: () => void q.refetch(),
	};
}
