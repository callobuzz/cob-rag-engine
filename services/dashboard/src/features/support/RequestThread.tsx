import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { formatDateTime } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { supportStrings } from "@/lib/strings/support";
import { cn } from "@/lib/utils";
import { supportRequestQuery, useAddSupportMessage, useCloseSupportRequest } from "./api";
import { RequestStatus } from "./RequestStatus";

const s = supportStrings.thread;

/** One request with its thread, a box to add to it and a button to mark it done. */
export function RequestThread({ requestId }: { requestId: string }) {
	const id = useId();
	const { productName } = useBranding();
	const q = useQuery(supportRequestQuery(requestId));
	const add = useAddSupportMessage(requestId);
	const close = useCloseSupportRequest(requestId);
	const [text, setText] = useState("");
	const [failure, setFailure] = useState<string | null>(null);

	if (q.isPending) return <CardSkeleton lines={5} />;
	if (q.isError)
		return <ErrorState error={q.error} title={supportStrings.loadError} onRetry={() => void q.refetch()} />;
	const r = q.data;

	const send = async (e: React.FormEvent) => {
		e.preventDefault();
		setFailure(null);
		if (text.trim() === "") return;
		try {
			await add.mutateAsync(text);
			setText("");
			toast.success(s.sentToast);
		} catch (err) {
			setFailure(messageFor(toApiError(err)).title);
		}
	};
	const markDone = async () => {
		setFailure(null);
		try {
			await close.mutateAsync();
			toast.success(s.closed);
		} catch (err) {
			setFailure(messageFor(toApiError(err)).title);
		}
	};

	return (
		<div data-testid="support-thread" className="grid gap-4 p-4">
			<div className="flex flex-wrap items-start justify-between gap-3">
				<div className="min-w-0">
					<h2 className="text-[16px] font-bold break-words">{r.subject}</h2>
					<p className="m-0 text-[12.5px] text-muted-foreground">
						{fill(s.title, { number: `#${r.number}` })}
						{r.page ? ` · ${fill(s.page, { page: r.page })}` : ""}
					</p>
				</div>
				<RequestStatus status={r.status} />
			</div>
			<ol className="m-0 grid list-none gap-3 p-0">
				{r.messages.map((m) => (
					<li
						key={m.id}
						data-testid={`message-${m.author_kind}`}
						className={cn(
							"grid gap-1 rounded-lg border p-3",
							m.author_kind === "deployer" ? "border-primary/40 bg-primary/5" : "border-border bg-card",
						)}
					>
						<div className="flex flex-wrap items-center justify-between gap-2 text-[12.5px] text-muted-foreground">
							<strong className="text-foreground">
								{m.author_kind === "deployer" ? fill(s.provider, { product: productName }) : (m.author ?? s.you)}
							</strong>
							<time dateTime={m.created_at}>{formatDateTime(m.created_at)}</time>
						</div>
						<p className="m-0 text-[13.5px] whitespace-pre-wrap break-words">{m.body}</p>
					</li>
				))}
			</ol>
			{r.status === "closed" ? <p className="m-0 text-[13px] text-muted-foreground">{s.closedNote}</p> : null}
			<form onSubmit={(e) => void send(e)} noValidate className="grid gap-3">
				{failure ? <FormAlert>{failure}</FormAlert> : null}
				<Field id={id} label={s.reply} hint={r.status === "closed" ? s.replyHint : undefined}>
					<Textarea id={id} rows={4} value={text} onChange={(e) => setText(e.target.value)} />
				</Field>
				<div className="flex flex-wrap gap-2">
					<SubmitButton variant="default" pending={add.isPending} pendingLabel={s.sending}>
						{s.send}
					</SubmitButton>
					{r.status !== "closed" ? (
						<Button type="button" variant="outline" disabled={close.isPending} onClick={() => void markDone()}>
							{close.isPending ? s.closing : s.close}
						</Button>
					) : null}
				</div>
			</form>
		</div>
	);
}
