import { Link } from "@tanstack/react-router";
import { Plus, X } from "lucide-react";
import { type ReactNode, useId } from "react";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Segmented } from "@/components/patterns/Segmented";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useDashboardFeatures } from "@/lib/features";
import { fill } from "@/lib/strings/en";
import { playgroundStrings as t } from "@/lib/strings/playground";
import type { LlmOverride } from "./api";
import { ModelOverride } from "./ModelOverride";
import {
	CATEGORY_MAX,
	EMPTY_TRY,
	type SearchModeChoice,
	SYSTEM_PROMPT_MAX,
	type TriState,
	type TryField,
	type TryState,
} from "./try-settings";

/**
 * Mockup `.try-panel`: settings for this question only — `QueryOptions` and a one-off system
 * prompt. Knowledge-base settings (preset, re-ranker, blend weight) are linked, not faked.
 */
export function TrySettingsPanel({
	id,
	kbId,
	value,
	onChange,
	errors,
	override,
	onOverride,
}: {
	id: string;
	kbId: string;
	value: TryState;
	onChange: (v: TryState) => void;
	errors: Partial<Record<TryField, string>>;
	override: LlmOverride;
	onOverride: (v: LlmOverride) => void;
}) {
	const uid = useId();
	const models = useDashboardFeatures().shown("models");
	const set = <K extends keyof TryState>(k: K, v: TryState[K]) => onChange({ ...value, [k]: v });
	const tri = (k: "hyde" | "classification" | "validation", label: string, help?: string) => (
		<Field id={`${uid}-${k}`} label={label} help={help}>
			<NativeSelect
				id={`${uid}-${k}`}
				value={value[k]}
				onChange={(e) => set(k, e.target.value as TriState)}
				aria-describedby={help ? `${uid}-${k}-help` : undefined}
			>
				<option value="">{t.try.useSaved}</option>
				<option value="on">{t.try.on}</option>
				<option value="off">{t.try.off}</option>
			</NativeSelect>
		</Field>
	);
	const num = (k: "topK" | "finalK", label: string, help: string) => (
		<Field id={`${uid}-${k}`} label={label} help={help} error={errors[k]}>
			<Input
				id={`${uid}-${k}`}
				inputMode="numeric"
				value={value[k]}
				placeholder={t.try.useSaved}
				aria-invalid={!!errors[k] || undefined}
				aria-describedby={`${uid}-${k}-help`}
				onChange={(e) => set(k, e.target.value)}
			/>
		</Field>
	);

	return (
		<section id={id} aria-label={t.try.title} className="grid gap-3.5 border-b border-border bg-muted p-4">
			<div className="flex flex-wrap items-center justify-between gap-3">
				<b>{t.try.title}</b>
				<span className="text-[12.5px] text-muted-foreground">{t.try.nothingSaved}</span>
			</div>
			<div className="grid gap-1.5">
				<span id={`${uid}-mode`} className="text-[13px] font-semibold">
					{t.try.searchMode}
				</span>
				<Segmented<SearchModeChoice>
					label={t.try.searchMode}
					value={value.searchMode}
					onChange={(v) => set("searchMode", v)}
					options={[
						{ value: "", label: t.try.saved },
						{ value: "dense", label: t.try.meaning },
						{ value: "hybrid", label: t.try.hybrid },
						{ value: "keyword", label: t.try.keywords },
					]}
				/>
			</div>
			<div className="grid gap-3.5 min-[861px]:grid-cols-2">
				{num("topK", t.try.topK, t.try.topKHelp)}
				{num("finalK", t.try.finalK, t.try.finalKHelp)}
				{tri("hyde", t.try.hyde)}
				{tri("classification", t.try.classification)}
				{tri("validation", t.try.validation, t.try.validationHelp)}
				<Field id={`${uid}-category`} label={t.try.category} help={t.try.categoryHelp} error={errors.category}>
					<Input
						id={`${uid}-category`}
						value={value.category}
						maxLength={CATEGORY_MAX}
						aria-invalid={!!errors.category || undefined}
						aria-describedby={`${uid}-category-help`}
						onChange={(e) => set("category", e.target.value)}
					/>
				</Field>
			</div>
			<fieldset className="m-0 grid gap-2 border-0 p-0">
				<legend className="mb-1 p-0 text-[13px] font-semibold">{t.try.filters}</legend>
				<p className="m-0 text-xs text-muted-foreground">{t.try.filtersHelp}</p>
				{value.filters.map((f, i) => (
					// biome-ignore lint/suspicious/noArrayIndexKey: rows have no identity beyond their position
					<div key={i} className="flex items-center gap-2">
						<Input
							aria-label={`${t.try.filterKey} ${i + 1}`}
							placeholder={t.try.filterKey}
							value={f.key}
							onChange={(e) =>
								set(
									"filters",
									value.filters.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)),
								)
							}
						/>
						<span aria-hidden>=</span>
						<Input
							aria-label={`${t.try.filterValue} ${i + 1}`}
							placeholder={t.try.filterValue}
							value={f.value}
							onChange={(e) =>
								set(
									"filters",
									value.filters.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)),
								)
							}
						/>
						<Button
							type="button"
							size="icon-sm"
							variant="ghost"
							aria-label={fill(t.try.removeFilter, { n: i + 1 })}
							onClick={() =>
								set(
									"filters",
									value.filters.filter((_, j) => j !== i),
								)
							}
						>
							<X aria-hidden />
						</Button>
					</div>
				))}
				{errors.filters ? (
					<p role="alert" className="m-0 text-xs text-destructive">
						{errors.filters}
					</p>
				) : null}
				<div>
					<Button
						type="button"
						size="sm"
						variant="outline"
						onClick={() => set("filters", [...value.filters, { key: "", value: "" }])}
					>
						<Plus aria-hidden />
						{t.try.addFilter}
					</Button>
				</div>
			</fieldset>
			<Field id={`${uid}-prompt`} label={t.try.systemPrompt} help={t.try.systemPromptHelp} error={errors.systemPrompt}>
				<Textarea
					id={`${uid}-prompt`}
					rows={3}
					value={value.systemPrompt}
					maxLength={SYSTEM_PROMPT_MAX}
					aria-invalid={!!errors.systemPrompt || undefined}
					aria-describedby={`${uid}-prompt-help`}
					onChange={(e) => set("systemPrompt", e.target.value)}
				/>
			</Field>
			{models ? <ModelOverride value={override} onChange={onOverride} /> : null}
			<div className="flex flex-wrap items-center justify-between gap-3 text-[12.5px] text-foreground-2">
				<span>
					{t.try.kbSettings}{" "}
					<Link to="/kbs/$kbId" params={{ kbId }} search={{ tab: "retrieval" }} className="text-primary underline">
						{t.try.kbSettingsLink}
					</Link>
				</span>
				<Button type="button" size="sm" variant="ghost" onClick={() => onChange(EMPTY_TRY)}>
					{t.try.reset}
				</Button>
			</div>
		</section>
	);
}

function Field({
	id,
	label,
	help,
	error,
	children,
}: {
	id: string;
	label: string;
	help?: string;
	error?: string;
	children: ReactNode;
}) {
	return (
		<div className="grid min-w-0 content-start gap-1.5">
			<label htmlFor={id} className="text-[13px] font-semibold">
				{label}
			</label>
			{children}
			{error ? (
				<p role="alert" className="m-0 text-xs text-destructive">
					{error}
				</p>
			) : null}
			{help ? (
				<p id={`${id}-help`} className="m-0 text-xs text-muted-foreground">
					{help}
				</p>
			) : null}
		</div>
	);
}
