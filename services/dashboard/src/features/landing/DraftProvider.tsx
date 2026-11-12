import { useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { landingStrings as t } from "@/lib/strings/landing";
import { type LandingContent, type LandingLang, type LandingState, landingKeys, useSaveDraft } from "./api";
import { fieldId, locate, sameContent, type TabKey } from "./draft";

/**
 * The person's working copy of the website draft. Edits stay in memory until "Save draft"; a save
 * is made against the revision that was loaded, so a stale tab gets a conflict and keeps its
 * edits on screen.
 */
export interface Draft {
	state: LandingState;
	content: LandingContent;
	limits: Record<string, number>;
	/** The languages the site offers, default first. */
	languages: LandingLang[];
	defaultLanguage: LandingLang;
	dirty: boolean;
	conflict: boolean;
	saving: boolean;
	/** Change a copy of the draft in place. */
	update: (change: (draft: LandingContent) => void) => void;
	replace: (content: LandingContent) => void;
	save: () => Promise<LandingState | null>;
	reload: () => Promise<void>;
	/** Open the field a text path names (switches the tab, then focuses the input). */
	goTo: (path: string, lang?: LandingLang) => void;
	focusLang: LandingLang | null;
	pendingFocus: string | null;
	clearFocus: () => void;
	/** Set by the page frame so `goTo` can change tabs. */
	openTab: (tab: TabKey, sectionId?: string) => void;
	openSection: string | null;
}

const DraftContext = createContext<Draft | null>(null);

export function useDraft(): Draft {
	const draft = useContext(DraftContext);
	if (!draft) throw new Error("useDraft needs a DraftProvider");
	return draft;
}

export function DraftProvider({
	state,
	onTab,
	children,
}: {
	state: LandingState;
	onTab: (tab: TabKey) => void;
	children: ReactNode;
}) {
	const qc = useQueryClient();
	const saveMutation = useSaveDraft();
	const [content, setContent] = useState<LandingContent>(state.draft);
	const [conflict, setConflict] = useState(false);
	const [pendingFocus, setPendingFocus] = useState<string | null>(null);
	const [focusLang, setFocusLang] = useState<LandingLang | null>(null);
	const [openSection, setOpenSection] = useState<string | null>(null);
	const loadedRevision = useRef(state.draft_revision);

	// A save, a restore or a reload brought a newer server draft: start from it.
	useEffect(() => {
		if (state.draft_revision === loadedRevision.current) return;
		loadedRevision.current = state.draft_revision;
		setContent(state.draft);
		setConflict(false);
	}, [state.draft_revision, state.draft]);

	const dirty = useMemo(() => !sameContent(content, state.draft), [content, state.draft]);

	const update = useCallback((change: (draft: LandingContent) => void) => {
		setContent((prev) => {
			const next = structuredClone(prev) as LandingContent;
			change(next);
			return next;
		});
	}, []);

	const save = useCallback(async (): Promise<LandingState | null> => {
		try {
			const saved = await saveMutation.mutateAsync({ base_revision: state.draft_revision, content });
			setConflict(false);
			toast.success(t.status.saved);
			return saved;
		} catch (e) {
			const err = toApiError(e);
			if (err.code === "landing_revision_conflict") setConflict(true);
			else toast.error(messageFor(err).title);
			return null;
		}
	}, [saveMutation, state.draft_revision, content]);

	const reload = useCallback(async () => {
		await qc.invalidateQueries({ queryKey: landingKeys.state });
		const fresh = qc.getQueryData<LandingState>(landingKeys.state);
		if (fresh) {
			loadedRevision.current = fresh.draft_revision;
			setContent(fresh.draft);
		}
		setConflict(false);
	}, [qc]);

	const openTab = useCallback(
		(tab: TabKey, sectionId?: string) => {
			onTab(tab);
			setOpenSection(sectionId ?? null);
		},
		[onTab],
	);

	const goTo = useCallback(
		(path: string, lang?: LandingLang) => {
			const where = locate(path);
			openTab(where.tab, where.sectionId);
			setPendingFocus(fieldId(path));
			setFocusLang(lang ?? null);
		},
		[openTab],
	);

	const value: Draft = {
		state,
		content,
		limits: state.limits,
		languages: content.languages,
		defaultLanguage: content.default_language,
		dirty,
		conflict,
		saving: saveMutation.isPending,
		update,
		replace: setContent,
		save,
		reload,
		goTo,
		focusLang,
		pendingFocus,
		clearFocus: () => setPendingFocus(null),
		openTab,
		openSection,
	};
	return <DraftContext.Provider value={value}>{children}</DraftContext.Provider>;
}
