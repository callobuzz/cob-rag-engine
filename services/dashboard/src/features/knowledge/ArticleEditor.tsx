import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { Bold, Heading2, List } from "lucide-react";
import { useEffect, useId, useRef } from "react";
import { useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ErrorState } from "@/components/patterns/ErrorState";
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
import { messageFor } from "@/lib/api/error-messages";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { Markdown } from "@/lib/markdown";
import { knowledgeStrings } from "@/lib/strings/knowledge";
import { cn } from "@/lib/utils";
import { type ArticleKind, articleQuery, useSaveArticle } from "./api";
import { applyFormat, type Format } from "./markdown-edit";

const t = knowledgeStrings.editor;
export const MAX_BODY = 50_000;
export const MAX_TITLE = 200;

const schemaFor = (kind: ArticleKind) =>
	z.object({
		title: z
			.string()
			.trim()
			.min(1, kind === "faq" ? t.questionRequired : t.titleRequired)
			.max(MAX_TITLE, t.titleLong),
		body: z
			.string()
			.trim()
			.min(1, kind === "faq" ? t.answerRequired : t.bodyRequired)
			.max(MAX_BODY, t.bodyLong),
	});
type Form = { title: string; body: string };

/**
 * Write or change an article (`kind="article"`) or a question and answer (`kind="faq"`) of a
 * knowledge base. `id` (the document or article id) opens an existing one: its text is loaded exactly
 * as it was written. Saving teaches the assistant; the engine answers 202 and the list shows the
 * document once it has been learned.
 */
export function ArticleEditor({
	kb,
	kind,
	id,
	initialTitle, // TV4
	open,
	onOpenChange,
	onSaved,
}: {
	kb: string;
	kind: ArticleKind;
	id: string | null;
	/** TV4: the question to start a new editor with (from the value report). */
	initialTitle?: string;
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onSaved: (title: string) => void;
}) {
	const faq = kind === "faq";
	const heading = faq ? (id ? t.faqEditTitle : t.faqTitle) : id ? t.articleEditTitle : t.articleTitle;
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-[680px]">
				<DialogHeader>
					<DialogTitle>{heading}</DialogTitle>
					<DialogDescription>{faq ? t.faqLead : t.articleLead}</DialogDescription>
				</DialogHeader>
				{open ? (
					<Body
						kb={kb}
						kind={kind}
						id={id}
						initialTitle={initialTitle}
						onDone={() => onOpenChange(false)}
						onSaved={onSaved}
					/>
				) : null}
			</DialogContent>
		</Dialog>
	);
}

function Body({
	kb,
	kind,
	id,
	initialTitle,
	onDone,
	onSaved,
}: {
	kb: string;
	kind: ArticleKind;
	id: string | null;
	initialTitle?: string; // TV4
	onDone: () => void;
	onSaved: (title: string) => void;
}) {
	const existing = useQuery({ ...articleQuery(kb, id ?? ""), enabled: id !== null });
	if (id !== null && existing.isPending) {
		return (
			<div aria-busy="true">
				<span className="sr-only">{t.loading}</span>
				<CardSkeleton lines={3} />
			</div>
		);
	}
	if (id !== null && (existing.isError || !existing.data)) {
		return <ErrorState error={existing.error} title={t.loadError} onRetry={() => void existing.refetch()} />;
	}
	return (
		<EditorForm
			kb={kb}
			kind={kind}
			id={id}
			initial={{ title: existing.data?.title ?? initialTitle ?? "", body: existing.data?.body ?? "" }}
			onDone={onDone}
			onSaved={onSaved}
		/>
	);
}

function EditorForm({
	kb,
	kind,
	id,
	initial,
	onDone,
	onSaved,
}: {
	kb: string;
	kind: ArticleKind;
	id: string | null;
	initial: Form;
	onDone: () => void;
	onSaved: (title: string) => void;
}) {
	const faq = kind === "faq";
	const ids = { title: useId(), body: useId() };
	const area = useRef<HTMLTextAreaElement | null>(null);
	const save = useSaveArticle(kb);
	const form = useAppForm<Form>({ resolver: zodResolver(schemaFor(kind)), defaultValues: initial });
	const errs = form.formState.errors;
	const body = useWatch({ control: form.control, name: "body" }) ?? "";
	useEffect(() => {
		form.setFocus("title");
	}, [form]);

	const submit = useAppSubmit(
		form,
		async (v) => {
			await save.mutateAsync({
				id: id ?? undefined,
				input: { kind, title: v.title.trim(), body: v.body.trim() },
			});
			toast.success(id ? t.savedEdit : t.saved);
			onSaved(v.title.trim());
			onDone();
		},
		{ fieldName: (path) => (path === "title" || path === "body" ? path : null) },
	);

	const format = (f: Format) => {
		const el = area.current;
		const text = form.getValues("body");
		const edit = applyFormat(text, el?.selectionStart ?? text.length, el?.selectionEnd ?? text.length, f);
		form.setValue("body", edit.text, { shouldDirty: true, shouldValidate: form.formState.isSubmitted });
		requestAnimationFrame(() => {
			el?.focus();
			el?.setSelectionRange(edit.start, edit.end);
		});
	};
	const { ref: bodyRef, ...bodyProps } = form.register("body");

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
			{submit.error ? <FormAlert>{messageFor(submit.error).title}</FormAlert> : null}
			<Field id={ids.title} label={faq ? t.question : t.title} error={errs.title?.message}>
				<Input
					id={ids.title}
					autoComplete="off"
					maxLength={MAX_TITLE + 50}
					placeholder={faq ? t.questionPlaceholder : t.titlePlaceholder}
					aria-invalid={!!errs.title}
					aria-describedby={describedBy(ids.title, { error: !!errs.title })}
					{...form.register("title")}
				/>
			</Field>
			<Field
				id={ids.body}
				label={faq ? t.answer : t.body}
				hint={faq ? undefined : t.bodyHint}
				error={errs.body?.message}
				labelExtra={
					<span className="text-[12px] text-muted-foreground">
						{t.counter.replace("{n}", body.length.toLocaleString("en-IN"))}
					</span>
				}
			>
				{faq ? null : (
					<div role="toolbar" aria-label={t.toolbarLabel} className="flex gap-1.5">
						<Button type="button" size="sm" variant="outline" onClick={() => format("heading")}>
							<Heading2 aria-hidden />
							{t.heading}
						</Button>
						<Button type="button" size="sm" variant="outline" onClick={() => format("bullet")}>
							<List aria-hidden />
							{t.bullet}
						</Button>
						<Button type="button" size="sm" variant="outline" onClick={() => format("bold")}>
							<Bold aria-hidden />
							{t.bold}
						</Button>
					</div>
				)}
				<Textarea
					id={ids.body}
					rows={faq ? 4 : 10}
					className={cn("min-h-28", faq ? "" : "font-mono text-[13.5px]")}
					placeholder={faq ? t.answerPlaceholder : t.bodyPlaceholder}
					aria-invalid={!!errs.body}
					aria-describedby={describedBy(ids.body, { hint: !faq, error: !!errs.body })}
					{...bodyProps}
					ref={(el) => {
						bodyRef(el);
						area.current = el;
					}}
				/>
			</Field>
			{faq ? null : (
				<section
					aria-label={t.previewLabel}
					data-testid="article-preview"
					className="rounded-md border border-border bg-muted p-3"
				>
					<div className="mb-1 text-[12px] font-semibold tracking-wide text-muted-foreground uppercase">
						{t.previewLabel}
					</div>
					{body.trim() ? (
						<Markdown text={body} copyable={false} />
					) : (
						<p className="m-0 text-[13px] text-muted-foreground">{t.previewEmpty}</p>
					)}
				</section>
			)}
			<DialogFooter>
				<Button type="button" variant="ghost" onClick={onDone}>
					{t.cancel}
				</Button>
				<SubmitButton variant="default" pending={submit.isSubmitting} locked={submit.locked} pendingLabel={t.saving}>
					{t.save}
				</SubmitButton>
			</DialogFooter>
		</form>
	);
}
