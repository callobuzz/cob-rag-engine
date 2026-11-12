import { useEffect, useId, useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { fill } from "@/lib/strings/en";
import { landingStrings as t } from "@/lib/strings/landing";
import { cn } from "@/lib/utils";
import type { LandingLang } from "./api";
import { useDraft } from "./DraftProvider";
import { charCount, clampTo, fieldId, type LText, withLang } from "./draft";

/**
 * One text the visitor reads, in every language the website offers: a tab per language, a counter
 * against the engine's own limit for this field, and a plain "Missing" note where a language that
 * is switched on still lacks its text (publishing refuses gaps). `limitKey` is the field's key in
 * the `limits` the engine sent (`hero.headline`); there are no limits written in this file.
 */
export function LocalizedField({
	path,
	label,
	value,
	onChange,
	limitKey,
	long = false,
	required = false,
	hint,
	rows,
	maxOverride,
}: {
	/** The text's path in the draft (the same the engine reports), also its DOM id. */
	path: string;
	label: string;
	value: LText | null | undefined;
	onChange: (next: LText | null) => void;
	limitKey: string;
	long?: boolean;
	required?: boolean;
	hint?: string;
	rows?: number;
	/** A shorter limit than the field's own (a visitor's line in the sample conversation). */
	maxOverride?: number;
}) {
	const draft = useDraft();
	const uid = useId();
	const id = fieldId(path);
	const langs = draft.languages;
	const [lang, setLang] = useState<LandingLang>(draft.defaultLanguage);
	const inputRef = useRef<HTMLInputElement & HTMLTextAreaElement>(null);
	const current = langs.includes(lang) ? lang : draft.defaultLanguage;
	const own = draft.limits[limitKey] ?? null;
	const max = maxOverride != null && (own == null || maxOverride < own) ? maxOverride : own;
	const text = value?.[current] ?? "";
	const isDefault = current === draft.defaultLanguage;
	const missing = !isDefault && text.trim() === "";
	const warnings = draft.state.copy_warnings.filter((w) => w.path === path);

	// A "fix this text" link from the Languages tab or a failed publish lands here.
	useEffect(() => {
		if (draft.pendingFocus !== id) return;
		if (draft.focusLang && langs.includes(draft.focusLang)) setLang(draft.focusLang);
		const frame = requestAnimationFrame(() => {
			const el = document.getElementById(id);
			el?.scrollIntoView({ block: "center" });
			el?.focus();
			draft.clearFocus();
		});
		return () => cancelAnimationFrame(frame);
	}, [draft.pendingFocus, draft.focusLang, draft.clearFocus, id, langs]);

	const set = (raw: string) => {
		const clamped = clampTo(raw, max);
		const next = withLang(value, current, clamped, draft.defaultLanguage);
		onChange(Object.keys(next).length === 0 ? null : next);
	};

	const Control = long ? Textarea : Input;
	const used = charCount(text);
	return (
		<div className="grid content-start gap-1.5" data-testid={`field-${path}`}>
			<div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
				<label htmlFor={id} className="text-[13px] font-semibold">
					{label}
					{required ? <span className="text-destructive"> *</span> : null}
				</label>
				{langs.length > 1 ? (
					<fieldset className="m-0 flex gap-0.5 border-0 p-0">
						<legend className="sr-only">{`${label}: ${t.field.language}`}</legend>
						{langs.map((l) => {
							const empty = l !== draft.defaultLanguage && !(value?.[l] ?? "").trim();
							return (
								<button
									key={l}
									type="button"
									aria-pressed={l === current}
									aria-label={`${t.langs[l]}${empty ? `, ${t.field.missing}` : ""}`}
									onClick={() => setLang(l)}
									className={cn(
										"rounded-sm px-2 py-0.5 text-[12px] font-semibold",
										l === current ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground",
									)}
								>
									{t.langsShort[l]}
									{empty ? (
										<span aria-hidden className="ml-1 text-warning">
											•
										</span>
									) : null}
								</button>
							);
						})}
					</fieldset>
				) : null}
			</div>
			<Control
				id={id}
				ref={inputRef as never}
				lang={current === "hi" ? "hi" : undefined}
				value={text}
				rows={long ? (rows ?? 3) : undefined}
				aria-describedby={`${uid}-meta`}
				aria-invalid={missing || undefined}
				onChange={(e: { target: { value: string } }) => set(e.target.value)}
			/>
			<div id={`${uid}-meta`} className="flex flex-wrap items-center justify-between gap-x-3 text-[12.5px]">
				<span className={missing ? "font-medium text-warning" : "text-muted-foreground"}>
					{missing ? t.field.missing : (hint ?? "")}
				</span>
				{max != null ? (
					<span
						className={cn("tabular-nums", used >= max ? "font-medium text-warning" : "text-muted-foreground")}
						data-testid={`counter-${path}`}
					>
						{fill(t.field.counter, { used, max })}
					</span>
				) : null}
			</div>
			{warnings.map((w) => (
				<p key={`${w.word}-${w.sentence}`} role="note" className="m-0 text-[12.5px] text-warning">
					{fill(t.status.copyWarning, { word: w.word, sentence: w.sentence })}
				</p>
			))}
		</div>
	);
}
