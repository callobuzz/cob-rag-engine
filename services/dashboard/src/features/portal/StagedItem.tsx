import { RefreshCw } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
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
import { Switch } from "@/components/ui/switch";
import { MimeIcon } from "@/features/documents/ui";
import { formatDateTime } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { en, fill } from "@/lib/strings/en";
import { portalStrings as t } from "@/lib/strings/portal";
import { portal, type StagedContent, useWorkspaceMutation } from "./api";
import { toastPortalError } from "./messages";
import { AiStatusBadge } from "./ui";

const s = t.staging;

/** The name people gave it: the file name without the engine's ".txt"/".url" suffix for text and pages. */
export function stagedName(c: Pick<StagedContent, "original_filename" | "source_type">): string {
	const name = c.original_filename ?? "";
	if (c.source_type === "text_dump" && name === "text_dump.txt") return s.untitled;
	if (c.source_type === "url" && name === "url_source.url") return s.sources.url ?? s.untitled;
	if (c.source_type === "text_dump") return name.replace(/\.txt$/, "") || s.untitled;
	if (c.source_type === "url") return name.replace(/\.url$/, "") || s.untitled;
	return name || s.untitled;
}

function totalTokens(u: StagedContent["token_usage"]): number | null {
	const v = u?.total_tokens;
	return typeof v === "number" ? v : null;
}

/** One staged item: type, AI status (icon + text), output type, quality report, tokens, Reprocess. */
export function StagedItem({
	workspaceId,
	item,
	canReprocess,
}: {
	workspaceId: string;
	item: StagedContent;
	canReprocess: boolean;
}) {
	const [open, setOpen] = useState(false);
	const name = stagedName(item);
	const tokens = totalTokens(item.token_usage);
	const report = Object.entries(item.ai_quality_report ?? {});
	return (
		<li className="grid gap-2 border-b border-border px-4 py-3.5 last:border-b-0" data-testid="staged-item">
			<div className="flex flex-wrap items-center gap-3">
				<MimeIcon mime={item.mime_type} />
				<div className="grid min-w-0 flex-1 basis-[200px] gap-0.5">
					<b className="truncate text-[13.5px]">{name}</b>
					<span className="text-[12.5px] text-muted-foreground">
						{s.sources[item.source_type] ?? item.source_type} · {formatDateTime(item.created_at)}
					</span>
				</div>
				<AiStatusBadge status={item.ai_status} />
				{canReprocess && (item.ai_status === "completed" || item.ai_status === "failed") ? (
					<Button
						size="sm"
						variant="outline"
						onClick={() => setOpen(true)}
						aria-label={fill(s.reprocessTitle, { name })}
					>
						<RefreshCw aria-hidden />
						{s.reprocess}
					</Button>
				) : null}
			</div>
			<div className="flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-foreground-2">
				{item.ai_output_type ? <span>{fill(s.outputType, { type: item.ai_output_type })}</span> : null}
				{tokens != null ? <span>{fill(s.tokens, { n: formatNumber(tokens) })}</span> : null}
			</div>
			<details className="text-[12.5px] text-foreground-2">
				<summary className="cursor-pointer font-semibold">{s.qualityShow}</summary>
				{report.length ? (
					<dl className="mt-1.5 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1">
						{report.map(([k, v]) => (
							<div key={k} className="contents">
								<dt className="font-mono text-muted-foreground">{k}</dt>
								<dd className="m-0 break-words">{typeof v === "string" ? v : JSON.stringify(v)}</dd>
							</div>
						))}
					</dl>
				) : (
					<p className="mt-1.5 mb-0">{s.qualityEmpty}</p>
				)}
			</details>
			<ReprocessDialog open={open} onOpenChange={setOpen} workspaceId={workspaceId} item={item} name={name} />
		</li>
	);
}

function ReprocessDialog({
	open,
	onOpenChange,
	workspaceId,
	item,
	name,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	workspaceId: string;
	item: StagedContent;
	name: string;
}) {
	const ids = { force: useId(), model: useId() };
	const form = useAppForm<{ force: boolean; model: string }>({
		defaultValues: { force: item.ai_status === "completed", model: "" },
	});
	const force = form.watch("force");
	const run = useWorkspaceMutation(workspaceId, (v: { force: boolean; model: string }) =>
		portal.reprocess(workspaceId, item.id, { force: v.force, model_override: v.model.trim() || null }),
	);
	const submit = useAppSubmit(
		form,
		async (v) => {
			await run.mutateAsync(v);
			toast.success(s.reprocessed);
			onOpenChange(false);
		},
		{
			onError: (err) => {
				toastPortalError(err);
				return true;
			},
		},
	);
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-md">
				<form noValidate className="grid gap-4" onSubmit={(e) => void submit.onSubmit(e)}>
					<DialogHeader>
						<DialogTitle>{fill(s.reprocessTitle, { name })}</DialogTitle>
						<DialogDescription>{s.reprocessLead}</DialogDescription>
					</DialogHeader>
					<div className="flex items-center gap-3">
						<Switch id={ids.force} checked={force} onCheckedChange={(c) => form.setValue("force", c)} />
						<label htmlFor={ids.force} className="text-[13px] font-semibold">
							{s.force}
						</label>
					</div>
					<Field id={ids.model} label={s.model} hint={s.modelHint}>
						<Input
							id={ids.model}
							className="font-mono"
							autoComplete="off"
							aria-describedby={`${ids.model}-hint`}
							{...form.register("model")}
						/>
					</Field>
					<DialogFooter>
						<Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
							{en.common.cancel}
						</Button>
						<SubmitButton variant="default" pending={submit.isSubmitting}>
							{s.reprocessSubmit}
						</SubmitButton>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
