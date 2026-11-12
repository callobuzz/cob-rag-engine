import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { Controller } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { Segmented } from "@/components/patterns/Segmented";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Panel } from "@/features/kbs/ui";
import { formatPercent } from "@/lib/format/number";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { fill } from "@/lib/strings/en";
import { portalStrings as t } from "@/lib/strings/portal";
import { contentQuery, invalidateWorkspace, isAiDone, portal, type Workspace } from "./api";
import { portalMessage } from "./messages";
import { StagedItem } from "./StagedItem";

const s = t.staging;
type Kind = "file" | "text" | "url";

export function isHttpUrl(v: string): boolean {
	try {
		const u = new URL(v.trim());
		return u.protocol === "http:" || u.protocol === "https:";
	} catch {
		return false;
	}
}

/**
 * Staging (D11): add a file (multipart through `upload.ts`, with progress), pasted text or a page
 * address (JSON `{source_type, content, title}`); the items with their AI status, polled every 3 s
 * until each is `completed` or `failed`.
 */
export function StagingPanel({ w, canAdd }: { w: Workspace; canAdd: boolean }) {
	const qc = useQueryClient();
	const q = useQuery(contentQuery(w.id));
	const items = q.data?.items ?? [];
	const working = items.filter((c) => !isAiDone(c)).length;

	// When an item finishes, its chunks exist: refresh the workspace (counts, chunks, audit).
	const prevWorking = useRef(working);
	useEffect(() => {
		if (working < prevWorking.current) void invalidateWorkspace(qc, w.id);
		prevWorking.current = working;
	}, [working, qc, w.id]);

	return (
		<div className="grid items-start gap-4 min-[1101px]:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
			{canAdd ? <AddContent w={w} /> : null}
			<Panel
				title={s.title}
				sub={s.sub}
				className={canAdd ? undefined : "min-[1101px]:col-span-2"}
				aside={
					working > 0 ? (
						<span className="text-[12.5px] text-muted-foreground" data-testid="staging-polling">
							{s.polling}
						</span>
					) : null
				}
			>
				{q.isPending ? (
					<div className="p-4">
						<CardSkeleton lines={2} />
					</div>
				) : q.isError ? (
					<div className="p-4">
						<ErrorState error={q.error} onRetry={() => void q.refetch()} />
					</div>
				) : items.length === 0 ? (
					<p className="m-0 p-4 text-foreground-2">{s.empty}</p>
				) : (
					<ul aria-label={s.listLabel} className="m-0 grid list-none p-0">
						{items.map((c) => (
							<StagedItem key={c.id} workspaceId={w.id} item={c} canReprocess={canAdd} />
						))}
					</ul>
				)}
			</Panel>
		</div>
	);
}

const addSchema = z
	.object({
		kind: z.enum(["file", "text", "url"]),
		file: z.custom<File | null>().nullable(),
		text: z.string(),
		title: z.string(),
		url: z.string(),
	})
	.superRefine((v, ctx) => {
		if (v.kind === "file" && !v.file) ctx.addIssue({ code: "custom", path: ["file"], message: s.fileRequired });
		if (v.kind === "text" && !v.text.trim()) ctx.addIssue({ code: "custom", path: ["text"], message: s.textRequired });
		if (v.kind === "url" && !isHttpUrl(v.url)) ctx.addIssue({ code: "custom", path: ["url"], message: s.urlBad });
	});
type AddValues = z.infer<typeof addSchema>;

function AddContent({ w }: { w: Workspace }) {
	const qc = useQueryClient();
	const [progress, setProgress] = useState<number | null>(null);
	const fileInput = useRef<HTMLInputElement>(null);
	const ids = { file: useId(), text: useId(), title: useId(), url: useId() };
	const form = useAppForm<AddValues>({
		resolver: zodResolver(addSchema),
		defaultValues: { kind: "file", file: null, text: "", title: "", url: "" },
	});
	const kind = form.watch("kind");
	const errs = form.formState.errors;

	const submit = useAppSubmit(
		form,
		async (v) => {
			try {
				if (v.kind === "file" && v.file) {
					setProgress(0);
					await portal.addFile(w.id, v.file, (f) => setProgress(f));
				} else if (v.kind === "text") {
					await portal.addText(w.id, { source_type: "text_dump", content: v.text, title: v.title.trim() || null });
				} else {
					await portal.addText(w.id, { source_type: "url", content: v.url.trim(), title: v.title.trim() || null });
				}
				toast.success(s.added);
				form.reset({ kind: v.kind, file: null, text: "", title: "", url: "" });
				if (fileInput.current) fileInput.current.value = "";
				await invalidateWorkspace(qc, w.id);
			} finally {
				setProgress(null);
			}
		},
		{
			onError: (err) => {
				if (err.status === 422 || err.status === 400) {
					const name = form.getValues("kind") === "file" ? "file" : form.getValues("kind") === "text" ? "text" : "url";
					form.setError(name, { type: "server", message: err.fieldErrors[0]?.message ?? err.detail });
					return true;
				}
				return false;
			},
		},
	);

	return (
		<Panel title={s.addLabel}>
			<form noValidate className="grid gap-4 p-4" onSubmit={(e) => void submit.onSubmit(e)}>
				<Segmented<Kind>
					label={s.kindLabel}
					value={kind}
					options={(["file", "text", "url"] as const).map((k) => ({ value: k, label: s.kinds[k] }))}
					onChange={(k) => {
						form.setValue("kind", k);
						form.clearErrors();
						submit.clearError();
					}}
				/>
				{submit.error ? <FormAlert>{portalMessage(submit.error, "upload").title}</FormAlert> : null}
				{kind === "file" ? (
					<Field id={ids.file} label={s.file} hint={s.fileHint} error={errs.file?.message}>
						<Controller
							control={form.control}
							name="file"
							render={({ field }) => (
								<Input
									ref={(el) => {
										fileInput.current = el;
										field.ref(el);
									}}
									id={ids.file}
									type="file"
									accept=".pdf,.docx,.pptx,.xlsx,.txt,.md,.html,.htm,.csv"
									onBlur={field.onBlur}
									onChange={(e) => field.onChange(e.target.files?.[0] ?? null)}
									aria-invalid={!!errs.file || undefined}
									aria-describedby={describedBy(ids.file, { hint: true, error: !!errs.file })}
								/>
							)}
						/>
					</Field>
				) : null}
				{kind === "text" ? (
					<Field id={ids.text} label={s.text} error={errs.text?.message}>
						<Textarea
							id={ids.text}
							rows={8}
							maxLength={500000}
							placeholder={s.textPlaceholder}
							aria-invalid={!!errs.text || undefined}
							aria-describedby={describedBy(ids.text, { error: !!errs.text })}
							{...form.register("text")}
						/>
					</Field>
				) : null}
				{kind === "url" ? (
					<Field id={ids.url} label={s.url} hint={s.urlHint} error={errs.url?.message}>
						<Input
							id={ids.url}
							type="url"
							inputMode="url"
							autoComplete="off"
							className="font-mono"
							placeholder={s.urlPlaceholder}
							aria-invalid={!!errs.url || undefined}
							aria-describedby={describedBy(ids.url, { hint: true, error: !!errs.url })}
							{...form.register("url")}
						/>
					</Field>
				) : null}
				{kind !== "file" ? (
					<Field id={ids.title} label={s.textTitle}>
						<Input
							id={ids.title}
							autoComplete="off"
							maxLength={500}
							placeholder={s.textTitlePlaceholder}
							{...form.register("title")}
						/>
					</Field>
				) : null}
				<div className="flex items-center justify-end gap-3">
					{progress !== null ? (
						<span className="text-[12.5px] text-muted-foreground" role="status">
							{fill(s.uploading, { pct: formatPercent(progress) })}
						</span>
					) : null}
					<SubmitButton variant="default" pending={submit.isSubmitting} pendingLabel={s.adding}>
						<Upload aria-hidden />
						{s.submit}
					</SubmitButton>
				</div>
			</form>
		</Panel>
	);
}
