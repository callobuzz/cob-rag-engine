import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { MessageSquare, Star, ThumbsDown, ThumbsUp } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import { toast } from "sonner";
import { ErrorState } from "@/components/patterns/ErrorState";
import { KeyValue } from "@/components/patterns/KeyValue";
import { Segmented } from "@/components/patterns/Segmented";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Button, buttonVariants } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { GoldenPairDialog } from "@/features/quality/GoldenPairDialog";
import { isApiError } from "@/lib/api/errors";
import type { Corpus } from "@/lib/api/queries";
import { formatDateTime } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { insightsStrings } from "@/lib/strings/insights";
import { type FeedbackItem, feedbackListQuery, type HistoryItem, queryDetailQuery, useAddFeedback } from "./api";
import { ConfidenceBadge } from "./confidence";
import { FeedbackRating } from "./FeedbackTab";
import { formatLatency } from "./QuestionsTab";
import { useErrorText, useKbNames } from "./ui";

const t = insightsStrings.drawer;
const CORRECTION_MAX = 5000;

/**
 * One question (`GET /query/{id}`): answer, model, tokens; its feedback (the latest 100 ratings
 * filtered by `query_id` on the client), "Add feedback", "Open in playground" and "Add to golden
 * set". A dialog with a heading (Sheet).
 */
export function QuestionDrawer({
	id,
	kb,
	row,
	onClose,
}: {
	id: string | undefined;
	kb: Corpus;
	/** The history row, when it is on the loaded page (its knowledge base and session). */
	row?: HistoryItem;
	onClose: () => void;
}) {
	return (
		<Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
			<SheetContent side="right" className="w-[520px] max-w-[94vw] gap-0 overflow-y-auto p-0">
				{id ? <DrawerBody id={id} kb={kb} row={row} /> : null}
			</SheetContent>
		</Sheet>
	);
}

function DrawerBody({ id, kb, row }: { id: string; kb: Corpus; row: HistoryItem | undefined }) {
	const q = useQuery(queryDetailQuery(id));
	const kbName = useKbNames();
	const corpusId = row?.corpus_id ?? kb.id;
	const [golden, setGolden] = useState(false);

	if (q.isPending) {
		return (
			<div className="grid gap-3 p-4" aria-busy="true">
				<SheetTitle>{t.title}</SheetTitle>
				<CardSkeleton lines={4} />
			</div>
		);
	}
	if (q.isError) {
		if (isApiError(q.error) && q.error.status === 404) {
			return (
				<div className="grid gap-2 p-4" data-testid="question-gone">
					<SheetTitle>{t.gone}</SheetTitle>
					<SheetDescription>{t.goneBody}</SheetDescription>
				</div>
			);
		}
		return (
			<div className="grid gap-3 p-4">
				<SheetTitle>{t.title}</SheetTitle>
				<ErrorState error={q.error} onRetry={() => void q.refetch()} />
			</div>
		);
	}
	const d = q.data;
	const usage = (d.token_usage ?? {}) as Record<string, unknown>;
	const n = (k: string) => (typeof usage[k] === "number" ? (usage[k] as number) : null);
	const total = n("total_tokens");
	const question = d.query_text ?? "";

	return (
		<>
			<SheetHeader className="border-b border-border p-4">
				<SheetTitle className="pr-8 break-words">{question || t.title}</SheetTitle>
				<SheetDescription asChild>
					<div className="flex flex-wrap items-center gap-2">
						<ConfidenceBadge score={d.confidence_score} />
						<span>{formatDateTime(d.created_at)}</span>
					</div>
				</SheetDescription>
			</SheetHeader>
			<div className="grid gap-5 p-4">
				<section aria-labelledby={`${id}-answer`} className="grid gap-2">
					<h3 id={`${id}-answer`} className="text-[13px] font-bold">
						{t.answer}
					</h3>
					<p data-testid="drawer-answer" className="m-0 text-[14px] leading-relaxed whitespace-pre-wrap">
						{d.response || t.noAnswer}
					</p>
					<p className="m-0 text-[12.5px] text-muted-foreground">{t.sources}</p>
				</section>
				<section aria-label={t.details} className="rounded-lg border border-border">
					<KeyValue
						rows={[
							{ label: t.model, value: d.model_used ?? "—" },
							{
								label: t.tokens,
								value:
									total == null
										? "—"
										: fill(t.tokensValue, {
												total: formatNumber(total),
												prompt: formatNumber(n("prompt_tokens") ?? 0),
												completion: formatNumber(n("completion_tokens") ?? 0),
											}),
							},
							{ label: t.latency, value: formatLatency(d.latency_ms) },
							{ label: t.kb, value: kbName(corpusId) },
							...(row?.session_id ? [{ label: t.session, value: row.session_id }] : []),
						]}
					/>
				</section>
				<div className="flex flex-wrap gap-2">
					<Link
						to="/playground"
						search={{ kb: corpusId, q: question.slice(0, 2000) || undefined }}
						className={buttonVariants({ variant: "outline" })}
					>
						<MessageSquare aria-hidden />
						{t.openPlayground}
					</Link>
					<Button type="button" onClick={() => setGolden(true)} disabled={!question}>
						<Star aria-hidden />
						{t.addGolden}
					</Button>
				</div>
				<FeedbackOnQuery queryId={id} corpusId={corpusId} />
				<AddFeedback queryId={id} />
			</div>
			<GoldenPairDialog
				open={golden}
				onOpenChange={setGolden}
				kbId={corpusId}
				kbName={kbName(corpusId)}
				pair={null}
				initial={{ query: question.slice(0, 5000), expected_answer: (d.response ?? "").slice(0, 10000) }}
			/>
		</>
	);
}

function FeedbackOnQuery({ queryId, corpusId }: { queryId: string; corpusId: string }) {
	const q = useQuery(feedbackListQuery({ corpusId, page: 1, limit: 100 }));
	const mine: FeedbackItem[] = (q.data?.items ?? []).filter((f) => f.query_id === queryId);
	return (
		<section aria-labelledby={`${queryId}-fb`} className="grid gap-2">
			<h3 id={`${queryId}-fb`} className="text-[13px] font-bold">
				{t.feedback}
			</h3>
			{q.isError ? (
				<ErrorState error={q.error} onRetry={() => void q.refetch()} />
			) : q.isPending ? null : mine.length === 0 ? (
				<p className="m-0 text-[13px] text-muted-foreground">{t.feedbackNone}</p>
			) : (
				<ul className="m-0 grid list-none gap-2 p-0" data-testid="drawer-feedback">
					{mine.map((f) => (
						<li key={f.id} className="grid gap-1 rounded-md border border-border p-3 text-[13px]">
							<div className="flex flex-wrap items-center gap-2">
								<FeedbackRating rating={f.rating} />
								<span className="text-muted-foreground">{formatDateTime(f.created_at)}</span>
							</div>
							{f.correction ? <p className="m-0 whitespace-pre-wrap">{f.correction}</p> : null}
						</li>
					))}
				</ul>
			)}
		</section>
	);
}

function AddFeedback({ queryId }: { queryId: string }) {
	const add = useAddFeedback();
	const errorText = useErrorText();
	const [rating, setRating] = useState<"up" | "down">("down");
	const [correction, setCorrection] = useState("");
	const id = useId();
	const onSubmit = (e: FormEvent) => {
		e.preventDefault();
		add.mutate(
			{ query_id: queryId, rating: rating === "up" ? 5 : 1, correction: correction.trim() || null },
			{
				onSuccess: () => {
					setCorrection("");
					toast.success(t.feedbackSaved);
				},
				onError: (err) => toast.error(errorText(err)),
			},
		);
	};
	return (
		<form onSubmit={onSubmit} className="grid gap-3 rounded-lg border border-border p-3" aria-label={t.addFeedback}>
			<h3 className="text-[13px] font-bold">{t.addFeedback}</h3>
			<Segmented
				label={t.addFeedback}
				value={rating}
				options={[
					{
						value: "up",
						label: (
							<>
								<ThumbsUp className="size-3.5" aria-hidden />
								{t.helpful}
							</>
						),
					},
					{
						value: "down",
						label: (
							<>
								<ThumbsDown className="size-3.5" aria-hidden />
								{t.notHelpful}
							</>
						),
					},
				]}
				onChange={setRating}
			/>
			<div className="grid gap-1.5">
				<label htmlFor={id} className="text-[13px] font-semibold">
					{t.correction}
				</label>
				<Textarea
					id={id}
					rows={3}
					maxLength={CORRECTION_MAX}
					value={correction}
					aria-describedby={`${id}-hint`}
					onChange={(e) => setCorrection(e.target.value)}
				/>
				<small id={`${id}-hint`} className="text-[12.5px] text-muted-foreground">
					{t.correctionHint}
				</small>
			</div>
			<div>
				<Button type="submit" variant="default" disabled={add.isPending}>
					{t.sendFeedback}
				</Button>
			</div>
		</form>
	);
}
