import { useQuery } from "@tanstack/react-query";
import { Link, useRouterState } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Controller, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { toApiError } from "@/lib/api/errors";
import { infoQuery } from "@/lib/api/queries";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { fill } from "@/lib/strings/en";
import { kbsStrings as t } from "@/lib/strings/kbs";
import { type CorpusDetail, presetsQuery, useUpdateCorpus } from "../api";
import { KbErrorAlert } from "../KbErrorAlert";
import { PresetCards, presetLabel } from "../PresetCards";
import {
	describeValue,
	engineDefault,
	matchingPreset,
	RETRIEVAL_FIELDS,
	RETRIEVAL_KEYS,
	type RetrievalConfigInput,
	type RetrievalDefaults,
	type RetrievalKey,
	type RetrievalValues,
	valuesFromConfig,
} from "../retrieval-fields";
import { type RetrievalConfigForm, validateRetrievalValue } from "../schemas";
import { FormFoot, Panel } from "../ui";
import { type FormValue, RetrievalField } from "./RetrievalField";
import { fieldFromPath, toastSaveError, useUnsavedGuard } from "./shared";

declare module "@tanstack/react-router" {
	interface HistoryState {
		/** D9 "Copy to Retrieval tab": shown as unsaved changes, never saved by itself (G-15). */
		retrievalPrefill?: RetrievalConfigInput;
	}
}

/** Stored values → form values (numbers as typed text). */
export function toForm(v: RetrievalValues): RetrievalConfigForm {
	const out = {} as RetrievalConfigForm;
	for (const k of RETRIEVAL_KEYS) {
		const x = v[k];
		out[k] = typeof x === "number" ? String(x) : x;
	}
	return out;
}

/** One form value → what the API takes (null = remove the field; engine default applies). */
export function toApi(key: RetrievalKey, v: FormValue): boolean | number | string | null {
	if (v === null || v === "") return null;
	const def = RETRIEVAL_FIELDS.find((f) => f.key === key);
	if (def?.control.kind === "number" && typeof v === "string") return Number(v.trim());
	return v;
}

/** An unset field's value as a form value (numbers as text), from GET /info. */
export function fallbackValue(key: RetrievalKey, engine: RetrievalDefaults | null | undefined): FormValue {
	const v = engineDefault(key, engine);
	return typeof v === "number" ? String(v) : v;
}

/**
 * "default: …" text: what the engine runs when the field is unset, e.g. "on (engine)" or
 * "Weighted scores 0.75 (engine)". `denseWeight` is the effective weight a convex default uses.
 */
export function describeDefault(
	key: RetrievalKey,
	engine: RetrievalDefaults | null | undefined,
	denseWeight?: FormValue,
): string {
	if (!engine) return t.retrieval.engineDefault;
	const words = describeValue(key, engineDefault(key, engine), {
		denseWeight,
		on: t.retrieval.on,
		off: t.retrieval.off,
	});
	const v = words ?? (key === "rerank_llm_model" ? t.retrieval.fastModel : null);
	return v === null ? t.retrieval.engineDefault : fill(t.retrieval.engineValue, { v });
}

/** A field's effective value: what is typed or stored, else the engine's default. */
export function effectiveValue(
	key: RetrievalKey,
	values: Partial<Record<RetrievalKey, FormValue>>,
	engine: RetrievalDefaults | null | undefined,
): FormValue {
	const v = values[key];
	return v === null || v === "" || v === undefined ? fallbackValue(key, engine) : v;
}

/** Plain 422 "Invalid retrieval config: final_k: 101 out of range [1, 100]; …" → per field. */
export function boundsErrors(detail: string): Partial<Record<RetrievalKey, string>> {
	const out: Partial<Record<RetrievalKey, string>> = {};
	const body = detail.replace(/^Invalid retrieval config:\s*/, "");
	for (const part of body.split(";")) {
		const m = /^\s*(\w+):\s*(.+)$/.exec(part);
		if (m?.[1] && (RETRIEVAL_KEYS as readonly string[]).includes(m[1])) out[m[1] as RetrievalKey] = m[2]?.trim() ?? "";
	}
	return out;
}

/** Retrieval (mockup `t-retrieval`): preset cards, then every advanced field. Members may save. */
export function RetrievalTab({ kb }: { kb: CorpusDetail }) {
	const presets = useQuery(presetsQuery);
	const update = useUpdateCorpus(kb.id);
	const saved = valuesFromConfig(kb.retrieval_config);
	const current = presets.data ? matchingPreset(kb.retrieval_config, presets.data.presets) : null;
	const [chosen, setChosen] = useState<string | null>(null);
	const [open, setOpen] = useState(true);
	const [resetting, setResetting] = useState<RetrievalKey | null>(null);
	const selected = chosen ?? current ?? "standard";
	// Unset fields run with the engine's own settings (a preset is only stored values).
	const engine = useQuery(infoQuery).data?.retrieval_defaults;

	const form = useAppForm<RetrievalConfigForm>({ defaultValues: toForm(saved) });
	const dirty = form.formState.isDirty;
	const guard = useUnsavedGuard(dirty);
	const values = useWatch({ control: form.control }) as RetrievalConfigForm;

	// D9 prefill from router history state: shown as unsaved changes with a banner.
	const prefill = useRouterState({ select: (s) => s.location.state.retrievalPrefill });
	const applied = useRef(false);
	useEffect(() => {
		if (!prefill || applied.current) return;
		applied.current = true;
		for (const k of RETRIEVAL_KEYS) {
			const v = prefill[k];
			if (v === undefined) continue;
			form.setValue(k, typeof v === "number" ? String(v) : v, { shouldDirty: true });
		}
		if (prefill.preset) setChosen(prefill.preset);
	}, [prefill, form]);

	const choosePreset = (name: string) => {
		setChosen(name);
		const p = presets.data?.presets[name];
		if (!p) return;
		for (const [k, v] of Object.entries(p)) {
			if (!(RETRIEVAL_KEYS as readonly string[]).includes(k)) continue;
			const key = k as RetrievalKey;
			form.setValue(key, typeof v === "number" ? String(v) : (v as boolean | string), { shouldDirty: true });
		}
	};

	const showErrors = (e: unknown) => {
		const err = toApiError(e);
		const byField: Partial<Record<RetrievalKey, string>> = {};
		for (const f of err.fieldErrors) {
			const name = fieldFromPath(f.path, "retrieval_config");
			if (name && (RETRIEVAL_KEYS as readonly string[]).includes(name)) byField[name as RetrievalKey] = f.message;
		}
		if (err.status === 422 && !err.fieldErrors.length) Object.assign(byField, boundsErrors(err.detail));
		const keys = Object.keys(byField) as RetrievalKey[];
		if (keys.length) for (const k of keys) form.setError(k, { message: byField[k] });
		else toastSaveError(e);
	};

	const submit = useAppSubmit(
		form,
		async (v) => {
			const d = form.formState.dirtyFields as Partial<Record<RetrievalKey, boolean>>;
			const body: Record<string, boolean | number | string | null> = {};
			for (const k of RETRIEVAL_KEYS) if (d[k]) body[k] = toApi(k, v[k]);
			if (Object.keys(body).length === 0) return;
			const next = await update.mutateAsync({ retrieval_config: body });
			form.reset(toForm(valuesFromConfig(next.retrieval_config)));
			setChosen(null);
			toast.success(fill(t.detail.savedToast, { what: t.retrieval.savedWhat }), {
				description: "Takes effect on the next question.",
			});
		},
		{
			fieldName: (path) => {
				const name = fieldFromPath(path, "retrieval_config");
				return name && (RETRIEVAL_KEYS as readonly string[]).includes(name) ? (name as RetrievalKey) : null;
			},
			// A 422 with only a "Invalid retrieval config: key: message; …" detail still names fields.
			onError: (err) => {
				if (err.status !== 422 || err.fieldErrors.length) return false;
				const byField = boundsErrors(err.detail);
				const keys = Object.keys(byField) as RetrievalKey[];
				for (const k of keys) form.setError(k, { type: "server", message: byField[k] });
				return keys.length > 0;
			},
		},
	);

	const resetOne = async (key: RetrievalKey) => {
		setResetting(key);
		try {
			const next = await update.mutateAsync({ retrieval_config: { [key]: null } });
			const v = valuesFromConfig(next.retrieval_config)[key];
			form.resetField(key, { defaultValue: typeof v === "number" ? String(v) : v });
			form.clearErrors(key);
		} catch (e) {
			showErrors(e);
		} finally {
			setResetting(null);
		}
	};

	const resetToPreset = async () => {
		const p = presets.data?.presets[selected] ?? {};
		const body: Record<string, string | null> = { preset: selected };
		for (const k of RETRIEVAL_KEYS) if (!(k in p)) body[k] = null;
		try {
			const next = await update.mutateAsync({ retrieval_config: body });
			form.reset(toForm(valuesFromConfig(next.retrieval_config)));
			setChosen(null);
			toast.success(fill(t.retrieval.resetToPresetDone, { preset: presetLabel(selected) }));
		} catch (e) {
			showErrors(e);
		}
	};

	// Dimming follows effective values: fusion left unset is convex by default, so the dense
	// weight is in use.
	const isActive = (dep?: { key: RetrievalKey; value: boolean | string }) =>
		!dep || effectiveValue(dep.key, values, engine) === dep.value;
	const denseWeight = effectiveValue("hybrid_dense_weight", values, engine);

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate>
			<Panel
				title={t.retrieval.title}
				sub={t.retrieval.sub}
				aside={
					<Badge variant={current ? "info" : "default"}>
						{current ? fill(t.retrieval.current, { preset: presetLabel(current) }) : t.retrieval.custom}
					</Badge>
				}
			>
				<div className="grid gap-3 p-4">
					{submit.error ? <KbErrorAlert error={submit.error} /> : null}
					{prefill ? <FormAlert tone="info">{t.retrieval.prefill}</FormAlert> : null}
					<PresetCards
						label={t.retrieval.title}
						value={selected}
						current={current}
						presets={presets.data?.presets}
						onChange={choosePreset}
					/>
					{presets.isError ? <FormAlert tone="warn">{t.retrieval.presetsError}</FormAlert> : null}
				</div>
				<div className="border-t border-border">
					<button
						type="button"
						aria-expanded={open}
						onClick={() => setOpen((o) => !o)}
						className="flex w-full items-center gap-2 px-4 py-3.5 text-left font-bold"
					>
						<ChevronRight className={open ? "size-4 rotate-90" : "size-4"} aria-hidden />
						{t.retrieval.advanced}
						<span className="ml-auto text-[12.5px] font-medium text-muted-foreground">{t.retrieval.advancedSub}</span>
					</button>
					{open ? (
						<div className="grid border-t border-border min-[1101px]:grid-cols-2 min-[1101px]:[&>div:nth-child(odd)]:border-r">
							{RETRIEVAL_FIELDS.map((def) => (
								<Controller
									key={def.key}
									control={form.control}
									name={def.key}
									rules={{ validate: (v) => validateRetrievalValue(def.key, v) }}
									render={({ field, fieldState }) => (
										<RetrievalField
											def={def}
											value={field.value}
											fallback={fallbackValue(def.key, engine)}
											defaultText={describeDefault(def.key, engine, denseWeight)}
											onChange={field.onChange}
											onBlur={field.onBlur}
											fieldRef={field.ref}
											onReset={() => void resetOne(def.key)}
											resetting={resetting === def.key}
											error={fieldState.error?.message}
											inactive={!isActive("dependsOn" in def ? def.dependsOn : undefined)}
										/>
									)}
								/>
							))}
						</div>
					) : null}
				</div>
				<FormFoot>
					<Button type="button" variant="ghost" disabled={update.isPending} onClick={() => void resetToPreset()}>
						{t.retrieval.resetToPreset}
					</Button>
					<Link to="/playground" search={{ kb: kb.id }} className={buttonVariants({ variant: "outline" })}>
						{t.retrieval.try}
					</Link>
					<Button
						type="button"
						variant="ghost"
						disabled={!dirty}
						onClick={() => {
							form.reset(toForm(saved));
							setChosen(null);
						}}
					>
						{t.detail.discard}
					</Button>
					<SubmitButton
						variant="default"
						pending={submit.isSubmitting}
						locked={submit.locked}
						pendingLabel={t.detail.saving}
					>
						{t.detail.save}
					</SubmitButton>
				</FormFoot>
			</Panel>
			{guard}
		</form>
	);
}
