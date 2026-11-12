import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { UploadTab } from "@/features/add-content/AddContentPage";
import { uploadLimits } from "@/features/add-content/api";
import { isFinishedRow, useUploadItems } from "@/features/add-content/upload-queue";
import { infoQuery } from "@/lib/api/queries";
import { fill } from "@/lib/strings/en";
import { onboardingStrings } from "@/lib/strings/onboarding";
import { type Onboarding, onboardingQuery } from "./api";

const t = onboardingStrings.brochures;

/**
 * Step 2 (TV5): "Add your brochures, price lists and menus". Optional: the same upload zone as the
 * Knowledge page, on the business's own knowledge base, and a line that says how many files the
 * assistant has read. Looks again every few seconds while any file is still being read.
 */
export function BrochureStep({
	state: initial,
	onNext,
	onBack,
}: {
	state: Onboarding;
	onNext: () => void;
	onBack: () => void;
}) {
	const kb = initial.kb_id;
	const info = useQuery(infoQuery);
	const items = useUploadItems(kb ?? "");
	// A file is in flight from the moment it is chosen to upload until its job has finished.
	const inFlight = items.some(
		(it) => it.phase === "waiting" || it.phase === "uploading" || (it.phase === "sent" && !isFinishedRow(it)),
	);
	const live = useQuery({
		...onboardingQuery(false),
		initialData: initial,
		refetchInterval: (q) => {
			const files = q.state.data?.files;
			return inFlight || (files && files.count > files.learned) ? 5_000 : false;
		},
	});
	const state = live.data ?? initial;
	// The engine's count changes when a file is accepted and again when it is read: look at both.
	const refetch = live.refetch;
	const wasInFlight = useRef(false);
	useEffect(() => {
		if (wasInFlight.current !== inFlight) void refetch();
		wasInFlight.current = inFlight;
	}, [inFlight, refetch]);
	const queued = items.length;
	if (!kb) {
		return (
			<div className="grid gap-3">
				<p className="m-0 text-[13.5px] text-foreground-2">{t.noKb}</p>
				<div>
					<Button onClick={onBack}>{onboardingStrings.back}</Button>
				</div>
			</div>
		);
	}
	const files = state.files ?? { count: 0, learned: 0 };
	const reading = files.count > files.learned;
	const added = files.count > 0 || queued > 0;
	return (
		<div className="grid gap-5">
			<div>
				<h2 className="text-lg font-bold">{t.title}</h2>
				<p className="m-0 text-[13.5px] text-foreground-2">{t.lead}</p>
			</div>
			<UploadTab kb={kb} limits={info.data ? uploadLimits(info.data) : null} />
			<p className="m-0 text-[12.5px] text-muted-foreground">{t.submitHint}</p>
			{files.count > 0 ? (
				<p data-testid="brochure-status" role="status" className="m-0 text-[13.5px] font-semibold">
					{reading
						? `${t.learning} ${fill(t.learned, { n: String(files.learned), total: String(files.count) })}`
						: t.allLearned}
				</p>
			) : null}
			<div className="flex flex-wrap items-center justify-between gap-2">
				<Button variant="ghost" onClick={onBack}>
					{onboardingStrings.back}
				</Button>
				<Button onClick={onNext}>{added ? onboardingStrings.next : t.skip}</Button>
			</div>
		</div>
	);
}
