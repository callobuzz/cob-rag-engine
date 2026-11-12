import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useId, useState } from "react";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { en, fill } from "@/lib/strings/en";
import { portalStrings as t } from "@/lib/strings/portal";
import type { Chunk } from "./api";

const s = t.split;

/** A split position is valid between 1 and len − 1 (the engine's bounds). */
export function validSplit(position: number, text: string): boolean {
	return Number.isInteger(position) && position >= 1 && position < text.length;
}

/** A sensible default: the paragraph or sentence break nearest the middle. */
export function suggestSplit(text: string): number {
	const mid = Math.floor(text.length / 2);
	let best = -1;
	for (const re of [/\n\s*\n/g, /[.!?।]\s+/g, /\s+/g]) {
		for (const m of text.matchAll(re)) {
			const at = (m.index ?? 0) + m[0].length;
			if (at >= 1 && at < text.length && (best < 0 || Math.abs(at - mid) < Math.abs(best - mid))) best = at;
		}
		if (best > 0) return best;
	}
	return Math.max(1, Math.min(text.length - 1, mid));
}

/** Split a chunk at a character position: click in the text or type the position; both halves preview. */
export function SplitDialog({
	chunk,
	n,
	pending,
	onOpenChange,
	onSplit,
}: {
	chunk: Chunk | null;
	n: number;
	pending: boolean;
	onOpenChange: (open: boolean) => void;
	onSplit: (position: number) => void;
}) {
	return (
		<Dialog open={!!chunk} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-2xl">
				{chunk ? (
					<SplitForm chunk={chunk} n={n} pending={pending} onCancel={() => onOpenChange(false)} onSplit={onSplit} />
				) : null}
			</DialogContent>
		</Dialog>
	);
}

function SplitForm({
	chunk,
	n,
	pending,
	onCancel,
	onSplit,
}: {
	chunk: Chunk;
	n: number;
	pending: boolean;
	onCancel: () => void;
	onSplit: (position: number) => void;
}) {
	const text = chunk.text;
	const max = text.length - 1;
	const ids = { text: useId(), pos: useId() };
	const [schema] = useState(() =>
		z.object({
			position: z.string().refine((v) => validSplit(Number(v), text), { message: fill(s.positionBad, { max }) }),
		}),
	);
	const form = useAppForm<{ position: string }>({
		resolver: zodResolver(schema),
		defaultValues: { position: String(suggestSplit(text)) },
	});
	const value = form.watch("position");
	const position = Number(value);
	const ok = validSplit(position, text);
	const err = form.formState.errors.position?.message;
	const submit = useAppSubmit(form, (v) => onSplit(Number(v.position)));
	useEffect(() => {
		form.setFocus("position");
	}, [form]);

	return (
		<form noValidate className="grid gap-4" onSubmit={(e) => void submit.onSubmit(e)}>
			<DialogHeader>
				<DialogTitle>{fill(s.title, { n })}</DialogTitle>
				<DialogDescription>{s.lead}</DialogDescription>
			</DialogHeader>
			<Field id={ids.text} label={fill(t.chunks.text, { n })}>
				<Textarea
					id={ids.text}
					readOnly
					value={text}
					rows={8}
					className="text-[13.5px]"
					onSelect={(e) => {
						const at = e.currentTarget.selectionStart;
						if (at >= 1 && at < text.length) form.setValue("position", String(at), { shouldValidate: true });
					}}
				/>
			</Field>
			<Field id={ids.pos} label={s.position} hint={fill(s.positionHint, { max })} error={err}>
				<Input
					id={ids.pos}
					type="number"
					inputMode="numeric"
					min={1}
					max={max}
					step={1}
					className="w-[160px]"
					aria-invalid={!!err || undefined}
					aria-describedby={describedBy(ids.pos, { hint: true, error: !!err })}
					{...form.register("position")}
				/>
			</Field>
			{ok ? (
				<div className="grid gap-3 min-[600px]:grid-cols-2">
					<section aria-label={s.first} className="grid gap-1 rounded-md border border-border bg-muted p-3">
						<b className="text-[12.5px] text-foreground-2">{s.first}</b>
						<p className="m-0 line-clamp-6 text-[13px] break-words whitespace-pre-wrap">
							{text.slice(0, position).trimEnd()}
						</p>
					</section>
					<section aria-label={s.second} className="grid gap-1 rounded-md border border-border bg-muted p-3">
						<b className="text-[12.5px] text-foreground-2">{s.second}</b>
						<p className="m-0 line-clamp-6 text-[13px] break-words whitespace-pre-wrap">
							{text.slice(position).trimStart()}
						</p>
					</section>
				</div>
			) : null}
			<DialogFooter>
				<Button type="button" variant="ghost" onClick={onCancel}>
					{en.common.cancel}
				</Button>
				<SubmitButton variant="default" pending={pending}>
					{s.submit}
				</SubmitButton>
			</DialogFooter>
		</form>
	);
}
