import { useQuery } from "@tanstack/react-query";
import { useId, useMemo } from "react";
import type { UseFormReturn } from "react-hook-form";
import { useWatch } from "react-hook-form";
import { describedBy, Field } from "@/components/auth/Field";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Skeleton } from "@/components/ui/skeleton";
import {
	type AnswerBenchmarks,
	type AnswerModelResult,
	answerBenchmarksQuery,
	findAnswer,
	friendlyName,
	ms,
	pct0,
	perThousand,
} from "@/features/benchmarks/api";
import { TagBadges } from "@/features/benchmarks/parts";
import { type Currency, useCurrency } from "@/lib/format/currency";
import { benchmarksStrings as bt } from "@/lib/strings/benchmarks";
import { fill } from "@/lib/strings/en";
import { languageModelsStrings as t } from "@/lib/strings/language-models";
import type { LangMode, LlmConfigForm } from "../schemas";

/** The languages with a row, in the order shown, and the form field each one edits. */
export const LANGUAGE_ROWS = [
	{ code: "en", field: "lang_en" },
	{ code: "hi", field: "lang_hi" },
	{ code: "hi-Latn", field: "lang_hi_latn" },
] as const;
type RowField = (typeof LANGUAGE_ROWS)[number]["field"];

/** The models offered in every row: the two the answer benchmark picked, and the cheap one. */
const RECOMMENDED = ["google/gemma-4-31b-it", "openai/gpt-oss-120b", "openai/gpt-oss-20b"] as const;

type Stored = Record<string, string>;

/** What the knowledge base stores as `language_models` → the form's mode and rows. */
export function languageFormFrom(stored: unknown): Pick<LlmConfigForm, "lang_mode" | RowField> {
	const map = stored && typeof stored === "object" && !Array.isArray(stored) ? (stored as Stored) : null;
	const pick = (code: string) => (typeof map?.[code] === "string" ? map[code] : "");
	return {
		lang_mode: map === null ? "default" : Object.keys(map).length === 0 ? "off" : "custom",
		lang_en: pick("en"),
		lang_hi: pick("hi"),
		lang_hi_latn: pick("hi-Latn"),
	};
}

/**
 * The `language_models` value a save sends: null removes it (the engine setting applies), `{}` turns the
 * switch off, a map names a model per language. A row left on "same as main model" sends nothing,
 * except where the engine would route that language and this knowledge base has no model of its own:
 * then the main model is named, because an omitted language would fall through to the engine's choice.
 * Languages this form has no row for (stored through the API) are kept.
 */
export function languageModelsToSend(
	v: Pick<LlmConfigForm, "lang_mode" | RowField>,
	opts: { stored: unknown; engine: Stored; kbModel: string; mainModel: string },
): Stored | null {
	if (v.lang_mode === "default") return null;
	if (v.lang_mode === "off") return {};
	const shown = new Set<string>(LANGUAGE_ROWS.map((r) => r.code));
	const kept: Stored = {};
	if (opts.stored && typeof opts.stored === "object" && !Array.isArray(opts.stored)) {
		for (const [code, model] of Object.entries(opts.stored as Stored)) {
			if (!shown.has(code) && typeof model === "string") kept[code] = model;
		}
	}
	const out: Stored = { ...kept };
	for (const { code, field } of LANGUAGE_ROWS) {
		const chosen = v[field].trim();
		if (chosen) out[code] = chosen;
		else if (opts.engine[code] && !opts.kbModel && opts.mainModel) out[code] = opts.mainModel;
	}
	return out;
}

/** The models the benchmark recommends for a language, then the others that meet the bar. */
function benchmarkOptions(
	data: AnswerBenchmarks,
	code: string,
): { top: AnswerModelResult[]; rest: AnswerModelResult[] } {
	const rec = data.recommendations;
	const keys =
		code === "en"
			? [rec.default, rec.cheapest_passing, rec.fastest_passing]
			: [rec.hindi_default, rec.hindi_budget, rec.default];
	const byKey = new Map(data.models.map((m) => [m.key, m]));
	const top: AnswerModelResult[] = [];
	for (const k of keys) {
		const m = k ? byKey.get(k) : undefined;
		if (m && !top.includes(m)) top.push(m);
	}
	const rest = data.models.filter((m) => m.passes && !top.includes(m));
	return { top, rest };
}

function optionText(m: AnswerModelResult, code: string, currency: Currency): string {
	const slice = m.by_language[code];
	return fill(bt.langPick.option, {
		name: friendlyName(m.model),
		pass: pct0(slice?.passed),
		cost: perThousand(slice?.usd_per_1000_answers, currency),
	});
}

/** What the benchmark says about the model chosen for one language. */
function ChosenLine({
	model,
	code,
	data,
	currency,
}: {
	model: string;
	code: string;
	data: AnswerBenchmarks | null;
	currency: Currency;
}) {
	const row = findAnswer(data?.models, model);
	if (!model || !data) return null;
	const slice = row?.by_language[code];
	if (!row || !slice) {
		return <small className="text-[12.5px] text-muted-foreground">{bt.langPick.notMeasured}</small>;
	}
	const values = {
		pass: pct0(slice.passed),
		declined: pct0(slice.refusal_correct_unanswerable),
		register: pct0(slice.register_ok),
		ttft: ms(slice.ttft_p50_ms),
		cost: perThousand(slice.usd_per_1000_answers, currency),
	};
	return (
		<span className="grid gap-1">
			<small className="text-[12.5px] text-foreground-2">
				{fill(slice.judged > 0 ? bt.langPick.lineHindi : bt.langPick.line, values)}
			</small>
			<span className="flex flex-wrap gap-1">
				<TagBadges tags={row.tags} selfHostable={row.self_hostable} />
			</span>
		</span>
	);
}

/** "Model per language" inside the Model & keys form: a mode and one model select per language. */
export function LanguageModels({
	form,
	readOnly,
	engine,
}: {
	form: UseFormReturn<LlmConfigForm>;
	readOnly: boolean;
	engine: Stored | null;
}) {
	const ids = { mode: useId(), en: useId(), hi: useId(), hiLatn: useId() };
	const rowIds: Record<RowField, string> = { lang_en: ids.en, lang_hi: ids.hi, lang_hi_latn: ids.hiLatn };
	const mode = useWatch({ control: form.control, name: "lang_mode" }) as LangMode;
	const values = useWatch({ control: form.control, name: ["lang_en", "lang_hi", "lang_hi_latn"] });
	const bench = useQuery(answerBenchmarksQuery);
	const data = bench.data?.benchmarks ?? null;
	const currency = useCurrency();
	const options = useMemo(
		() => (data ? Object.fromEntries(LANGUAGE_ROWS.map((r) => [r.code, benchmarkOptions(data, r.code)])) : null),
		[data],
	);
	const engineRoutes = engine ?? {};
	const engineSummary = Object.keys(engineRoutes).length
		? LANGUAGE_ROWS.filter((r) => engineRoutes[r.code])
				.map((r) => `${t.rows[r.code]?.label}: ${engineRoutes[r.code]}`)
				.join(", ")
		: t.engineNone;

	return (
		<fieldset className="m-0 grid min-w-0 gap-3 rounded-md border border-border p-3" aria-label={t.title}>
			<div className="grid gap-0.5">
				<b className="text-[13px]">{t.title}</b>
				<small className="text-[12.5px] text-muted-foreground">{t.lead}</small>
			</div>
			<Field id={ids.mode} label={t.mode} hint={`${t.modeHint} ${fill(t.engineSetting, { v: engineSummary })}.`}>
				<NativeSelect
					id={ids.mode}
					disabled={readOnly}
					aria-describedby={describedBy(ids.mode, { hint: true })}
					{...form.register("lang_mode")}
				>
					{(["default", "custom", "off"] as const).map((m) => (
						<option key={m} value={m}>
							{t.modes[m]}
						</option>
					))}
				</NativeSelect>
			</Field>
			{mode === "custom" && bench.isPending ? (
				<Skeleton className="h-24 w-full" />
			) : mode === "custom" ? (
				<>
					{LANGUAGE_ROWS.map(({ code, field }, i) => {
						const id = rowIds[field];
						const row = t.rows[code];
						const current = (values[i] ?? "").trim();
						const known = options
							? [...(options[code]?.top ?? []), ...(options[code]?.rest ?? [])].some((m) => m.model === current)
							: (RECOMMENDED as readonly string[]).includes(current);
						return (
							<Field key={code} id={id} label={row?.label ?? code} hint={row?.hint}>
								<NativeSelect
									id={id}
									disabled={readOnly}
									aria-describedby={describedBy(id, { hint: true })}
									{...form.register(field)}
								>
									<option value="">{t.sameAsMain}</option>
									{options ? (
										<>
											<optgroup label={bt.langPick.recommended}>
												{options[code]?.top.map((m) => (
													<option key={m.key} value={m.model}>
														{optionText(m, code, currency)}
													</option>
												))}
											</optgroup>
											{options[code]?.rest.length ? (
												<optgroup label={bt.langPick.alsoMeets}>
													{options[code]?.rest.map((m) => (
														<option key={m.key} value={m.model}>
															{optionText(m, code, currency)}
														</option>
													))}
												</optgroup>
											) : null}
										</>
									) : (
										RECOMMENDED.map((m) => (
											<option key={m} value={m}>
												{t.models[m] ?? m}
											</option>
										))
									)}
									{current && !known ? <option value={current}>{current}</option> : null}
								</NativeSelect>
								<ChosenLine model={current} code={code} data={data} currency={currency} />
							</Field>
						);
					})}
					<small className="text-[12.5px] text-muted-foreground">
						{t.whyBench} {t.thinkingNote}
					</small>
				</>
			) : null}
		</fieldset>
	);
}
