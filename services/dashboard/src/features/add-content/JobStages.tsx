import { CircleAlert, CircleCheck, CircleSlash, Clock, FileWarning, Loader2 } from "lucide-react";
import { contentStrings as t } from "@/lib/strings/content";
import { fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";

/** The real `JobStatus` path of an upload (pipeline/models.py). */
export const STAGES = [
	"queued",
	"uploading",
	"parsing",
	"chunking",
	"enriching",
	"embedding",
	"storing",
	"completed",
] as const;

export function stageLabel(status: string): string {
	return (t.stages as Record<string, string>)[status] ?? status;
}

/**
 * Step indicator for one job: a segmented bar (`role="progressbar"`, value = steps done) and the
 * current stage in text, so status is never colour alone. Failed jobs mark the step they failed
 * at (`errors.failed_at_step`); needs-review and expired jobs show their state instead of a bar.
 */
export function JobStages({ status, failedAt, name }: { status: string; failedAt?: string | null; name: string }) {
	if (status === "pending_review" || status === "expired" || status === "skipped") {
		const Icon = status === "expired" ? Clock : status === "skipped" ? CircleSlash : FileWarning;
		return (
			<p className="m-0 flex items-center gap-1.5 text-[13px] font-semibold text-warning">
				<Icon className="size-4" aria-hidden />
				{stageLabel(status)}
			</p>
		);
	}
	const failed = status === "failed";
	const at = failed
		? STAGES.indexOf((failedAt ?? "") as (typeof STAGES)[number])
		: STAGES.indexOf(status as (typeof STAGES)[number]);
	const index = at < 0 ? (failed ? 0 : 0) : at;
	const done = status === "completed";
	const total = STAGES.length;
	const value = done ? 100 : Math.round((index / (total - 1)) * 100);
	const current = done ? stageLabel("completed") : failed ? stageLabel("failed") : stageLabel(STAGES[index] ?? status);
	const Icon = done ? CircleCheck : failed ? CircleAlert : Loader2;
	return (
		<div className="grid gap-1.5">
			<div
				role="progressbar"
				aria-label={fill(t.upload.progress, { file: name })}
				aria-valuemin={0}
				aria-valuemax={100}
				aria-valuenow={value}
				aria-valuetext={`${current} (${index + 1}/${total})`}
				className="flex gap-1"
			>
				{STAGES.map((s, i) => (
					<span
						key={s}
						title={stageLabel(s)}
						className={cn(
							"h-1.5 flex-1 rounded-full bg-secondary",
							(done || i < index) && "bg-success",
							!done && !failed && i === index && "animate-pulse bg-primary motion-reduce:animate-none",
							failed && i === index && "bg-destructive",
						)}
					/>
				))}
			</div>
			<p
				className={cn(
					"m-0 flex items-center gap-1.5 text-[12.5px] font-semibold",
					done ? "text-success" : failed ? "text-destructive" : "text-foreground-2",
				)}
			>
				<Icon className={cn("size-3.5", !done && !failed && "animate-spin motion-reduce:animate-none")} aria-hidden />
				{current}
				{!done ? (
					<span className="font-normal text-muted-foreground">
						{failed && failedAt ? `· ${stageLabel(failedAt)}` : `· ${index + 1}/${total}`}
					</span>
				) : null}
			</p>
		</div>
	);
}
