import { CircleAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { messageFor } from "@/lib/api/error-messages";
import { useRagUrl } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { fill } from "@/lib/strings/en";
import { playgroundStrings as t } from "@/lib/strings/playground";
import { AnswerMeta } from "./AnswerMeta";
import { costUsd, type ModelEntry, tokensFrom } from "./cost";
import type { CompareRun, CompareSide } from "./session";
import { formatMs } from "./stages";
import { WhyTheyDiffer } from "./WhyTheyDiffer";

/** Mockup `#cmpView`: A "Saved settings" and B "Try settings" side by side, then "Why they differ". */
export function ComparePane({
	run,
	catalog,
	language,
}: {
	run: CompareRun | null;
	catalog: readonly ModelEntry[] | undefined;
	language: { code: string; label: string } | null;
}) {
	return (
		<div data-testid="compare" className="grid flex-1 content-start gap-3 p-4 min-[861px]:grid-cols-2">
			<p className="m-0 text-[12.5px] text-foreground-2 min-[861px]:col-span-2">{t.compare.note}</p>
			{!run ? (
				<p className="m-0 text-[13px] text-muted-foreground min-[861px]:col-span-2">{t.compare.empty}</p>
			) : (
				<>
					<p className="m-0 font-semibold min-[861px]:col-span-2">{run.question}</p>
					<Column title={t.compare.a} badge={t.compare.current} side={run.a} catalog={catalog} language={language} />
					<Column
						title={t.compare.b}
						badge={t.compare.tryBadge}
						info
						side={run.b}
						catalog={catalog}
						language={language}
					/>
					{run.a.response && run.b.response ? (
						<WhyTheyDiffer
							a={run.a.response}
							b={run.b.response}
							ra={run.a.retrieve}
							rb={run.b.retrieve}
							catalog={catalog}
						/>
					) : null}
				</>
			)}
		</div>
	);
}

function Column({
	title,
	badge,
	info,
	side,
	catalog,
	language,
}: {
	title: string;
	badge: string;
	info?: boolean;
	side: CompareSide;
	catalog: readonly ModelEntry[] | undefined;
	language: { code: string; label: string } | null;
}) {
	const ragUrl = useRagUrl();
	const { supportEmail } = useBranding();
	const r = side.response;
	const tokens = r ? tokensFrom(r.token_usage) : null;
	return (
		<section
			aria-label={title}
			data-testid="compare-column"
			aria-busy={side.status === "running"}
			className="grid content-start gap-2 rounded-md border border-border bg-muted p-3"
		>
			<h3 className="m-0 flex items-center justify-between gap-2 text-[13px] font-bold">
				{title}
				<Badge variant={info ? "info" : "default"}>{badge}</Badge>
			</h3>
			{side.status === "running" ? <p className="m-0 text-muted-foreground">{t.compare.running}</p> : null}
			{side.status === "error" && side.error ? (
				<p role="alert" className="m-0 flex items-start gap-1.5 text-[13px] text-destructive">
					<CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
					{messageFor(side.error, { ragUrl, support: supportEmail }).title}
				</p>
			) : null}
			{r ? (
				<>
					<div lang={language?.code} className="break-words whitespace-pre-wrap">
						{r.answer}
					</div>
					{side.retrieve ? (
						<p className="m-0 text-xs text-muted-foreground">
							{fill(t.compare.searchFor, { t: formatMs(side.retrieve.latency_ms) })}
						</p>
					) : null}
					<AnswerMeta
						confidence={r.confidence}
						model={r.model_used}
						latencyMs={r.latency_ms}
						tokens={tokens}
						costUsd={costUsd(catalog, r.model_used, tokens)}
					/>
				</>
			) : null}
		</section>
	);
}
