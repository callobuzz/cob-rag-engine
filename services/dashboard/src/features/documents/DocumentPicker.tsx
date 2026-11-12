import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Input } from "@/components/ui/input";
import { contentStrings as t } from "@/lib/strings/content";
import { fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";
import { documentsQuery } from "./api";
import { MimeIcon, useDebouncedValue } from "./ui";

export interface PickedDocument {
	id: string;
	name: string;
}

/**
 * Pick one document of a knowledge base by searching its name (merge target, upload replace /
 * version target). A search box plus a radio list: native, labelled and keyboard operable.
 */
export function DocumentPicker({
	kb,
	label,
	value,
	onChange,
	excludeId,
	invalid,
	describedBy,
}: {
	kb: string;
	label: string;
	value: PickedDocument | null;
	onChange: (v: PickedDocument | null) => void;
	excludeId?: string;
	invalid?: boolean;
	describedBy?: string;
}) {
	const id = useId();
	const [text, setText] = useState("");
	const search = useDebouncedValue(text.trim(), 300);
	const q = useQuery(documentsQuery(kb, { search: search || undefined, limit: 8 }));
	const items = (q.data?.items ?? []).filter((d) => d.id !== excludeId);

	return (
		<fieldset className="grid min-w-0 gap-2 border-0 p-0" aria-describedby={describedBy}>
			<legend className="mb-1.5 text-[13px] font-semibold">{label}</legend>
			<label htmlFor={id} className="sr-only">
				{fill("{label}: {hint}", { label, hint: t.picker.placeholder })}
			</label>
			<Input
				id={id}
				type="search"
				placeholder={t.picker.placeholder}
				value={text}
				aria-invalid={invalid || undefined}
				onChange={(e) => setText(e.target.value)}
			/>
			<div className="max-h-[220px] overflow-y-auto rounded-md border border-border">
				{q.isPending ? (
					<p className="m-0 p-3 text-[13px] text-muted-foreground">{t.picker.loading}</p>
				) : items.length === 0 ? (
					<p className="m-0 p-3 text-[13px] text-muted-foreground">{t.picker.none}</p>
				) : (
					<ul className="m-0 grid list-none p-0">
						{items.map((d) => {
							const name = d.file_name || d.external_id || d.id;
							const on = value?.id === d.id;
							return (
								<li key={d.id} className="border-b border-border last:border-b-0">
									<label
										className={cn(
											"flex cursor-pointer items-center gap-2.5 px-3 py-2 text-[13px] hover:bg-accent",
											on && "bg-primary-soft",
										)}
									>
										<input
											type="radio"
											name={`${id}-doc`}
											className="size-4 accent-[var(--primary)]"
											checked={on}
											onChange={() => onChange({ id: d.id, name })}
										/>
										<MimeIcon mime={d.mime_type} isRecord={!!d.external_id} className="size-7" />
										<span className="min-w-0 truncate font-medium">{name}</span>
									</label>
								</li>
							);
						})}
					</ul>
				)}
			</div>
			{value ? (
				<p className="m-0 text-[12.5px] text-foreground-2" aria-live="polite">
					{fill(t.picker.selected, { file: value.name })}
				</p>
			) : null}
		</fieldset>
	);
}
