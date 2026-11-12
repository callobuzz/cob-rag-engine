import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { PartyPopper } from "lucide-react";
import { useState } from "react";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Button } from "@/components/ui/button";
import { assistantStateQuery } from "@/features/assistant/api";
import { KbErrorAlert } from "@/features/kbs/KbErrorAlert";
import { toApiError } from "@/lib/api/errors";
import { fill } from "@/lib/strings/en";
import { setupStrings as t } from "@/lib/strings/setup";
import { type SetupSession, useApplySetup } from "../api";

/**
 * Step 6: publish (owners only). The draft revision is read again at click time: the owner was
 * told they may edit the draft in the Assistant tab between steps, and a stale revision is a
 * conflict, never a silent overwrite.
 */
export function PublishStep({
	kb,
	session,
	onBack,
	version,
}: {
	kb: string;
	session: SetupSession;
	onBack: () => void;
	version: number | null;
}) {
	const qc = useQueryClient();
	const apply = useApplySetup(kb, session.id);
	const [error, setError] = useState<unknown>(null);
	const [published, setPublished] = useState<number | null>(version);

	if (session.status === "applied" || published != null) {
		return (
			<div className="grid gap-4 rounded-lg border border-border bg-card p-5">
				<div className="flex items-center gap-2">
					<PartyPopper className="size-5 text-success" aria-hidden />
					<h2 className="m-0 text-lg font-semibold">{t.publish.liveTitle}</h2>
				</div>
				{published != null ? (
					<p className="m-0 text-[13px] text-muted-foreground">{fill(t.publish.liveBody, { version: published })}</p>
				) : null}
				<div className="flex flex-wrap gap-2">
					<Button variant="default" asChild>
						<Link to="/playground" search={{ kb }}>
							{t.publish.tryIt}
						</Link>
					</Button>
					<Button variant="outline" asChild>
						<Link to="/widget" search={{ kb }}>
							{t.publish.addToSite}
						</Link>
					</Button>
					<Button variant="ghost" asChild>
						<Link to="/kbs/$kbId" params={{ kbId: kb }} search={{ tab: "assistant" }}>
							{t.publish.openTab}
						</Link>
					</Button>
				</div>
			</div>
		);
	}

	const run = async () => {
		setError(null);
		try {
			const fresh = await qc.fetchQuery({ ...assistantStateQuery(kb), staleTime: 0 });
			const out = await apply.mutateAsync({ base_revision: fresh.draft?.draft_revision ?? 0 });
			setPublished(out.version);
		} catch (e) {
			if (toApiError(e).code === "assistant_draft_conflict")
				void qc.invalidateQueries({ queryKey: assistantStateQuery(kb).queryKey });
			setError(e);
		}
	};

	return (
		<div className="grid gap-4 rounded-lg border border-border bg-card p-5">
			<div className="grid gap-1">
				<h2 className="m-0 text-lg font-semibold">{t.publish.heading}</h2>
				<p className="m-0 text-[13px] text-muted-foreground">{t.publish.lead}</p>
			</div>
			{error ? <KbErrorAlert error={error} /> : null}
			{error != null && toApiError(error).code === "assistant_draft_conflict" ? (
				<p role="status" className="m-0 text-[13px] text-muted-foreground">
					{t.publish.conflict}
				</p>
			) : null}
			<div className="flex flex-wrap items-center justify-between gap-2">
				<Button variant="ghost" onClick={onBack}>
					{t.publish.back}
				</Button>
				<OwnerGate action="assistant.publish">
					<Button variant="default" disabled={apply.isPending} onClick={() => void run()}>
						{apply.isPending ? t.publish.publishing : t.publish.publish}
					</Button>
				</OwnerGate>
			</div>
		</div>
	);
}
