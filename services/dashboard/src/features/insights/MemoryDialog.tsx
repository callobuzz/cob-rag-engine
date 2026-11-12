import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useId } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { KeyValue } from "@/components/patterns/KeyValue";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { CardSkeleton } from "@/components/patterns/Skeletons";
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
import { formatDateTime } from "@/lib/format/date";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { en, fill } from "@/lib/strings/en";
import { insightsStrings } from "@/lib/strings/insights";
import { memoryQuery, useBulkCreateMemories, useCreateMemory } from "./api";
import { parseBulkMemories } from "./memory-bulk";
import { MEMORY_TYPES } from "./search";
import { useErrorText } from "./ui";

const t = insightsStrings.memoryDialog;
const types = insightsStrings.memories.types;

const formSchema = z.object({
	user_id: z
		.string()
		.trim()
		.min(1, t.required)
		.max(255, fill(t.tooLong, { n: 255 })),
	content: z
		.string()
		.trim()
		.min(1, t.required)
		.max(5000, fill(t.tooLong, { n: 5000 })),
	memory_type: z.enum(MEMORY_TYPES),
	category: z.string().max(50, fill(t.tooLong, { n: 50 })),
	related_entity: z.string().max(255, fill(t.tooLong, { n: 255 })),
	confidence: z.string().refine((v) => {
		const n = Number(v);
		return v.trim() !== "" && Number.isFinite(n) && n >= 0 && n <= 1;
	}, t.badConfidence),
});
type MemoryForm = z.infer<typeof formSchema>;

/** `POST …/memories` (one memory). */
export function MemoryDialog({
	open,
	onOpenChange,
	kb,
	kbName,
	defaultUser,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	kb: string;
	kbName: string;
	defaultUser?: string;
}) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-[560px]">
				{open ? (
					<MemoryFormBody kb={kb} kbName={kbName} defaultUser={defaultUser} onDone={() => onOpenChange(false)} />
				) : null}
			</DialogContent>
		</Dialog>
	);
}

function MemoryFormBody({
	kb,
	kbName,
	defaultUser,
	onDone,
}: {
	kb: string;
	kbName: string;
	defaultUser?: string;
	onDone: () => void;
}) {
	const ids = { user: useId(), content: useId(), type: useId(), category: useId(), entity: useId(), conf: useId() };
	const create = useCreateMemory(kb);
	const errorText = useErrorText();
	const form = useAppForm<MemoryForm>({
		resolver: zodResolver(formSchema),
		defaultValues: {
			user_id: defaultUser ?? "",
			content: "",
			memory_type: "fact",
			category: "",
			related_entity: "",
			confidence: "1",
		},
	});
	const errs = form.formState.errors;
	useEffect(() => {
		form.setFocus(defaultUser ? "content" : "user_id");
	}, [form, defaultUser]);

	const submit = useAppSubmit(
		form,
		async (v) => {
			await create.mutateAsync({
				user_id: v.user_id.trim(),
				content: v.content.trim(),
				memory_type: v.memory_type,
				category: v.category.trim() || null,
				related_entity: v.related_entity.trim() || null,
				confidence: Number(v.confidence),
				source: "explicit",
			});
			toast.success(t.saved);
			onDone();
		},
		{ fieldName: (path) => path.split(".")[0] as keyof MemoryForm },
	);
	const text = (
		id: string,
		key: keyof MemoryForm,
		label: string,
		extra: { hint?: string; placeholder?: string } = {},
	) => (
		<Field id={id} label={label} hint={extra.hint} error={errs[key]?.message}>
			<Input
				id={id}
				autoComplete="off"
				placeholder={extra.placeholder}
				aria-invalid={!!errs[key]}
				aria-describedby={describedBy(id, { hint: !!extra.hint, error: !!errs[key] })}
				{...form.register(key)}
			/>
		</Field>
	);

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
			<DialogHeader>
				<DialogTitle>{t.title}</DialogTitle>
				<DialogDescription>{fill(t.lead, { kb: kbName })}</DialogDescription>
			</DialogHeader>
			{submit.error ? <FormAlert>{errorText(submit.error)}</FormAlert> : null}
			{text(ids.user, "user_id", t.user, { hint: t.userHint })}
			<Field id={ids.content} label={t.content} error={errs.content?.message}>
				<Textarea
					id={ids.content}
					rows={3}
					placeholder={t.contentPlaceholder}
					aria-invalid={!!errs.content}
					aria-describedby={describedBy(ids.content, { error: !!errs.content })}
					{...form.register("content")}
				/>
			</Field>
			<div className="grid gap-4 min-[600px]:grid-cols-2">
				<Field id={ids.type} label={t.type}>
					<NativeSelect id={ids.type} {...form.register("memory_type")}>
						{MEMORY_TYPES.map((m) => (
							<option key={m} value={m}>
								{types[m]}
							</option>
						))}
					</NativeSelect>
				</Field>
				{text(ids.conf, "confidence", t.confidence, { hint: t.confidenceHint })}
				{text(ids.category, "category", t.category)}
				{text(ids.entity, "related_entity", t.entity, { placeholder: t.entityPlaceholder })}
			</div>
			<DialogFooter>
				<Button type="button" onClick={onDone}>
					{en.common.cancel}
				</Button>
				<SubmitButton variant="default" pending={submit.isSubmitting} locked={submit.locked}>
					{t.save}
				</SubmitButton>
			</DialogFooter>
		</form>
	);
}

const bulkSchema = z.object({
	json: z.string().superRefine((v, ctx) => {
		const r = parseBulkMemories(v);
		if (!r.ok) ctx.addIssue({ code: "custom", message: r.error });
	}),
});

/** `POST …/memories/bulk` from pasted JSON (≤ 50, each checked like the single form). */
export function BulkMemoryDialog({
	open,
	onOpenChange,
	kb,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	kb: string;
}) {
	const bulk = useBulkCreateMemories(kb);
	const errorText = useErrorText();
	const id = useId();
	const form = useAppForm<{ json: string }>({
		resolver: zodResolver(bulkSchema),
		defaultValues: { json: "" },
	});
	useEffect(() => {
		if (open) form.reset({ json: "" });
	}, [open, form]);
	const submit = useAppSubmit(form, async (v) => {
		const r = parseBulkMemories(v.json);
		if (!r.ok) return;
		const created = await bulk.mutateAsync(r.memories);
		const n = created.length;
		toast.success(n === 1 ? t.bulkSaved.one : fill(t.bulkSaved.many, { n }));
		onOpenChange(false);
	});
	const error = form.formState.errors.json?.message;
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-[620px]">
				<DialogHeader>
					<DialogTitle>{t.bulkTitle}</DialogTitle>
					<DialogDescription>{t.bulkLead}</DialogDescription>
				</DialogHeader>
				<form noValidate onSubmit={(e) => void submit.onSubmit(e)} className="grid gap-4">
					{submit.error ? <FormAlert>{errorText(submit.error)}</FormAlert> : null}
					<Field id={id} label={t.bulkLabel} error={error}>
						<Textarea
							id={id}
							rows={10}
							className="font-mono text-[12.5px]"
							aria-invalid={!!error || undefined}
							aria-describedby={describedBy(id, { error: !!error })}
							{...form.register("json")}
						/>
					</Field>
					<DialogFooter>
						<Button type="button" onClick={() => onOpenChange(false)}>
							{en.common.cancel}
						</Button>
						<SubmitButton variant="default" pending={submit.isSubmitting} locked={submit.locked}>
							{t.bulkSave}
						</SubmitButton>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}

/** "View" (`GET …/memories/{id}`). */
export function ViewMemoryDialog({ kb, id, onClose }: { kb: string; id: string | null; onClose: () => void }) {
	return (
		<Dialog open={!!id} onOpenChange={(o) => !o && onClose()}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-[560px]">
				<DialogHeader>
					<DialogTitle>{t.viewTitle}</DialogTitle>
					<DialogDescription className="sr-only">{t.viewTitle}</DialogDescription>
				</DialogHeader>
				{id ? <MemoryView kb={kb} id={id} /> : null}
			</DialogContent>
		</Dialog>
	);
}

function MemoryView({ kb, id }: { kb: string; id: string }) {
	const q = useQuery(memoryQuery(kb, id));
	if (q.isPending) return <CardSkeleton lines={4} />;
	if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
	const m = q.data;
	return (
		<div className="grid gap-3">
			<p className="m-0 text-[14px] whitespace-pre-wrap" data-testid="memory-content">
				{m.content}
			</p>
			<div className="rounded-lg border border-border">
				<KeyValue
					rows={[
						{ label: t.user, value: m.user_id ?? "—" },
						{ label: t.type, value: (types as Record<string, string>)[m.memory_type] ?? m.memory_type },
						{ label: t.category, value: m.category ?? "—" },
						{ label: t.related, value: m.related_entity ?? "—" },
						{ label: t.confidence, value: m.confidence == null ? "—" : m.confidence.toFixed(2) },
						{ label: t.source, value: m.source ?? "—" },
						{ label: t.created, value: formatDateTime(m.created_at) },
					]}
				/>
			</div>
		</div>
	);
}
