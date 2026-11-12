import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { assistantStateQuery } from "@/features/assistant/api";
import { useErrorText } from "@/features/insights/ui";
import { formatDate } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { reviewStrings as t } from "@/lib/strings/review";
import { AddAnswerDialog } from "./AddAnswerDialog";
import { AddRuleDialog } from "./AddRuleDialog";
import { type ReviewItem, useReviewAction } from "./api";

/** One group of questions with the fixes that suit it. */
export function ReviewItemCard({ kb, item }: { kb: string; item: ReviewItem }) {
	const act = useReviewAction(kb);
	const state = useQuery(assistantStateQuery(kb));
	const navigate = useNavigate();
	const errorText = useErrorText();
	const [dialog, setDialog] = useState<"answer" | "rule" | null>(null);
	// 0 = no draft yet (the engine creates it from the published version); null = not known yet.
	const revision = state.data ? (state.data.draft?.draft_revision ?? 0) : null;
	const added = () =>
		toast.success(t.addedToast, {
			action: {
				label: t.openAssistant,
				onClick: () => void navigate({ to: "/kbs/$kbId", params: { kbId: kb }, search: { tab: "assistant" } }),
			},
		});
	const fail = (e: unknown) => toast.error(errorText(e));
	// A ref, not `isPending`: two quick clicks land before React re-renders with the pending state.
	const busy = useRef(false);
	const send = (action: "added_document" | "dismissed", onSuccess: () => void) => {
		if (busy.current) return;
		busy.current = true;
		act.mutate(
			{ item_key: item.item_key, action },
			{ onSuccess, onError: fail, onSettled: () => (busy.current = false) },
		);
	};
	return (
		<article aria-label={item.title} className="rounded-lg border border-border bg-card p-4">
			<h3 className="m-0 text-base font-semibold">{item.title}</h3>
			<p className="m-0 text-foreground-2">
				{fill(item.count === 1 ? t.visitors.one : t.visitors.many, { n: item.count, date: formatDate(item.last_at) })}
			</p>
			{item.examples.length ? (
				<ul className="my-2 list-disc pl-5">
					{item.examples.slice(0, 5).map((e) => (
						<li key={`${e.at}${e.question}`}>{e.question}</li>
					))}
				</ul>
			) : null}
			<div className="mt-2 flex flex-wrap gap-2">
				{item.suggested.map((a) =>
					a === "added_answer" ? (
						<Button key={a} disabled={revision == null} onClick={() => setDialog("answer")}>
							{t.actions.added_answer}
						</Button>
					) : a === "added_rule" ? (
						<Button key={a} disabled={revision == null} onClick={() => setDialog("rule")}>
							{t.actions.added_rule}
						</Button>
					) : a === "added_document" ? (
						<Button
							key={a}
							disabled={act.isPending}
							onClick={() => send(a, () => void navigate({ to: "/add", search: { kb } }))}
						>
							{t.actions.added_document}
						</Button>
					) : (
						<Button
							key={a}
							disabled={act.isPending}
							variant="ghost"
							onClick={() => send("dismissed", () => toast.success(t.doneToast))}
						>
							{t.actions.dismissed}
						</Button>
					),
				)}
			</div>
			{dialog === "answer" && revision != null ? (
				<AddAnswerDialog kb={kb} item={item} revision={revision} onClose={() => setDialog(null)} onAdded={added} />
			) : null}
			{dialog === "rule" && revision != null ? (
				<AddRuleDialog kb={kb} item={item} revision={revision} onClose={() => setDialog(null)} onAdded={added} />
			) : null}
		</article>
	);
}
