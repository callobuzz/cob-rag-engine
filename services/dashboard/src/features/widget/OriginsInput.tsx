import { Globe, Plus, TriangleAlert, X } from "lucide-react";
import { type KeyboardEvent, type Ref, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fill } from "@/lib/strings/en";
import { widgetStrings as t } from "@/lib/strings/widget";
import { checkOrigin, MAX_ORIGINS, siteLabel, splitOrigins } from "./schemas";

/**
 * Allowed sites as chips: type or paste one or more addresses (Enter, comma or the Add button).
 * Each entry is checked like the engine does (scheme + host only); plain http only for
 * localhost/127.0.0.1; duplicates refused; `*` accepted with a warning.
 */
export function OriginsInput({
	id,
	value,
	onChange,
	error,
	describedBy,
	disabled,
	onBlur,
	inputRef,
}: {
	id: string;
	value: string[];
	onChange: (next: string[]) => void;
	error?: string;
	describedBy?: string;
	disabled?: boolean;
	/** react-hook-form's field.onBlur (validate after the first blur). */
	onBlur?: () => void;
	/** react-hook-form's field.ref (focus on a failed submit). */
	inputRef?: Ref<HTMLInputElement>;
}) {
	const [draft, setDraft] = useState("");
	const [entryError, setEntryError] = useState<string | null>(null);
	const errorId = `${id}-entry-error`;

	const add = () => {
		const parts = splitOrigins(draft);
		if (parts.length === 0) return;
		const next = [...value];
		const bad: string[] = [];
		const failed: string[] = [];
		for (const p of parts) {
			const r = checkOrigin(p, next);
			if (r.ok) next.push(r.origin);
			else {
				bad.push(r.error);
				failed.push(p);
			}
		}
		const capped = next.slice(0, MAX_ORIGINS);
		if (next.length > MAX_ORIGINS) bad.push(t.form.sitesTooMany);
		onChange(capped);
		setDraft(failed.join(" "));
		setEntryError(bad[0] ?? null);
	};

	const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
		if (e.key === "Enter" || e.key === ",") {
			e.preventDefault();
			add();
		}
	};

	const described = [describedBy, entryError ? errorId : null].filter(Boolean).join(" ") || undefined;

	return (
		<div className="grid gap-2">
			{value.length ? (
				<ul aria-label={t.origins.listLabel} className="m-0 flex list-none flex-wrap gap-1.5 p-0">
					{value.map((o) => (
						<li
							key={o}
							className="inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-secondary py-0.5 pr-0.5 pl-2.5 text-[12.5px]"
						>
							{o === "*" ? (
								<TriangleAlert className="size-3.5 shrink-0 text-warning" aria-hidden />
							) : (
								<Globe className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
							)}
							<span className="truncate font-mono">{o === "*" ? "*" : siteLabel(o)}</span>
							<button
								type="button"
								disabled={disabled}
								onClick={() => onChange(value.filter((x) => x !== o))}
								aria-label={fill(t.origins.remove, { origin: o })}
								className="grid size-6 place-items-center rounded-full text-muted-foreground hover:bg-card hover:text-foreground disabled:opacity-50"
							>
								<X className="size-3.5" aria-hidden />
							</button>
						</li>
					))}
				</ul>
			) : null}
			<div className="flex gap-2">
				<Input
					id={id}
					ref={inputRef}
					value={draft}
					disabled={disabled}
					inputMode="url"
					autoComplete="off"
					spellCheck={false}
					className="font-mono"
					placeholder={t.origins.placeholder}
					aria-invalid={!!(error || entryError)}
					aria-describedby={described}
					onChange={(e) => {
						setDraft(e.target.value);
						setEntryError(null);
					}}
					onKeyDown={onKey}
					onBlur={() => {
						if (draft.trim()) add();
						onBlur?.();
					}}
				/>
				<Button type="button" disabled={disabled} onClick={add} aria-label={t.origins.addLabel}>
					<Plus aria-hidden />
					{t.origins.add}
				</Button>
			</div>
			{entryError ? (
				<small id={errorId} className="text-[12.5px] font-medium text-destructive">
					{entryError}
				</small>
			) : null}
			{value.includes("*") ? (
				<small className="flex items-start gap-1.5 text-[12.5px] text-warning">
					<TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
					{t.origins.anyWarning}
				</small>
			) : null}
		</div>
	);
}
