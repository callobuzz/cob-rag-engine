import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { FieldValues, Path } from "react-hook-form";
import { toast } from "sonner";
import type { z } from "zod";
import type { ApiError } from "@/lib/api/errors";
import { qk } from "@/lib/api/query-keys";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { useUnsavedGuard } from "../kbs/detail/shared";
import { useSaveDraft } from "./api";
import { useAssistantDirty } from "./dirty";
import type { ConfigSection, FullConfig } from "./schemas";
import type { AssistantState } from "./types";

/** What the forms edit: the draft if there is one, else the live version. */
export function currentConfig(state: AssistantState): FullConfig {
	const cfg = state.draft?.config ?? state.published?.config;
	if (!cfg) throw new Error("no assistant config: the overview offers a template first");
	return cfg;
}

export function hasConfig(state: AssistantState): boolean {
	return state.draft != null || state.published != null;
}

/** The on/off switch belongs to the live version; a draft must carry the live value. */
export function liveEnabled(state: AssistantState, config: FullConfig): boolean {
	return state.published?.config.enabled ?? config.enabled;
}

/** Sections whose form value is not the config slice itself (`rules` is a list: form `{rules}`). */
export interface SectionMap<V, S> {
	toForm: (slice: S) => V;
	fromForm: (values: V) => S;
}

/**
 * Form and submit for one section; the unsaved-changes guard is separate (`useDraftSection`) so
 * tests can skip the router. Saving sends the WHOLE config with only this section replaced and
 * the draft's `base_revision`; a conflict is exposed, never shown as a field error.
 */
export function useDraftSectionCore<K extends ConfigSection, V extends FieldValues = FullConfig[K] & FieldValues>(
	kb: string,
	state: AssistantState,
	key: K,
	schema: z.ZodType,
	map?: SectionMap<V, FullConfig[K]>,
) {
	const save = useSaveDraft(kb);
	const qc = useQueryClient();
	const [conflict, setConflict] = useState<ApiError | null>(null);
	const config = currentConfig(state);
	const toForm = (c: FullConfig): V => (map ? map.toForm(c[key]) : (c[key] as unknown as V));
	const form = useAppForm<V>({
		resolver: zodResolver(schema as never) as never,
		defaultValues: toForm(config) as never,
	});
	const submit = useAppSubmit(
		form,
		async (values) => {
			setConflict(null);
			const latest = currentConfig(state);
			const slice = map ? map.fromForm(values as unknown as V) : (values as unknown as FullConfig[K]);
			const next: FullConfig = { ...latest, [key]: slice, enabled: liveEnabled(state, latest) };
			const out = await save.mutateAsync({ config: next, base_revision: state.draft?.draft_revision ?? 0 });
			form.reset(toForm(out.config) as never);
			toast.success(t.form.savedToast);
		},
		{
			fieldName: (path) => {
				const own = `config.${String(key)}.`;
				if (key === "rules" && path.startsWith("config.rules.")) return path.slice("config.".length) as Path<V>;
				return path.startsWith(own) ? (path.slice(own.length) as Path<V>) : null;
			},
			onError: (err) => {
				if (err.code !== "assistant_draft_conflict") return false;
				setConflict(err);
				return true;
			},
		},
	);
	const dirty = form.formState.isDirty;
	const { setDirty } = useAssistantDirty();
	useEffect(() => {
		setDirty(dirty);
		return () => setDirty(false);
	}, [dirty, setDirty]);
	return {
		form,
		submit,
		conflict,
		config,
		reloadTheirs: () => {
			setConflict(null);
			void qc.invalidateQueries({ queryKey: qk.assistant.state(kb) }).then(() => {
				const fresh = qc.getQueryData<AssistantState>(qk.assistant.state(kb));
				if (fresh && hasConfig(fresh)) form.reset(toForm(currentConfig(fresh)) as never);
			});
		},
	};
}

export function useDraftSection<K extends ConfigSection, V extends FieldValues = FullConfig[K] & FieldValues>(
	kb: string,
	state: AssistantState,
	key: K,
	schema: z.ZodType,
	map?: SectionMap<V, FullConfig[K]>,
) {
	const core = useDraftSectionCore<K, V>(kb, state, key, schema, map);
	const guard = useUnsavedGuard(core.form.formState.isDirty);
	return { ...core, guard };
}
