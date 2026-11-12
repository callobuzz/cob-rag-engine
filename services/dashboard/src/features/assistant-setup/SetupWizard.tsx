import { useQuery } from "@tanstack/react-query";
import { getRouteApi, Link } from "@tanstack/react-router";
import { useState } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/button";
import { assistantStateQuery } from "@/features/assistant/api";
import { corpusQuery, embeddingCatalogQuery } from "@/features/kbs/api";
import { isApiError } from "@/lib/api/errors";
import { fill } from "@/lib/strings/en";
import { kbsStrings } from "@/lib/strings/kbs";
import { setupStrings as t } from "@/lib/strings/setup";
import { cn } from "@/lib/utils";
import { type SetupSession, setupSessionQuery } from "./api";
import { CrawlStep } from "./steps/CrawlStep";
import { InterviewStep } from "./steps/InterviewStep";
import { ProposalStep } from "./steps/ProposalStep";
import { PublishStep } from "./steps/PublishStep";
import { ReviewStep } from "./steps/ReviewStep";
import { StartStep } from "./steps/StartStep";
import { TestsStep } from "./steps/TestsStep";

const route = getRouteApi("/_app/kbs/$kbId_/setup");

export const STEPS = ["website", "confirm", "questions", "review", "checks", "publish"] as const;
export type Step = (typeof STEPS)[number];

/** The step a session is on, from its status; the owner can step back (`forced`). */
export function stepOf(session: Pick<SetupSession, "status" | "website_url">, forced: Step | null): Step {
	if (forced) return forced;
	switch (session.status) {
		case "started":
			return session.website_url ? "website" : "confirm";
		case "proposed":
			return "confirm";
		case "interviewing":
			return "questions";
		case "generated":
			return "review";
		case "tested":
			return "checks";
		default:
			return "publish";
	}
}

/**
 * `/kbs/$kbId/setup?sid=`: the guided setup (C8). Without `sid` it offers the two ways to start;
 * with one it shows the step the session is on, so the URL resumes it after a reload.
 */
export function SetupWizard() {
	const { kbId } = route.useParams();
	const { sid } = route.useSearch();
	const navigate = route.useNavigate();
	const kb = useQuery(corpusQuery(kbId));
	const assistant = useQuery(assistantStateQuery(kbId));
	const [typeFirst, setTypeFirst] = useState(false);
	const goSession = (id: string | undefined) => void navigate({ search: id ? { sid: id } : {} });

	return (
		<div className="mx-auto grid w-full max-w-[820px] gap-4">
			<PageHeader
				title={t.wizard.title}
				description={t.wizard.lead}
				crumbs={[
					{ label: kbsStrings.list.title, to: "/kbs" },
					{ label: kb.data?.name ?? "…", to: `/kbs/${kbId}` },
					{ label: t.wizard.title },
				]}
				actions={
					<Button variant="ghost" asChild>
						<Link to="/kbs/$kbId" params={{ kbId }} search={{ tab: "assistant" }}>
							{t.wizard.exit}
						</Link>
					</Button>
				}
			/>
			{sid ? (
				<SessionView
					kb={kbId}
					sid={sid}
					onRestart={() => goSession(undefined)}
					onTypeInstead={() => {
						setTypeFirst(true);
						goSession(undefined);
					}}
				/>
			) : (
				<>
					<StepList current="website" />
					<StartStep kb={kbId} hasDraft={!!assistant.data?.draft} initialType={typeFirst} onStarted={goSession} />
				</>
			)}
		</div>
	);
}

function StepList({ current }: { current: Step }) {
	const at = STEPS.indexOf(current);
	return (
		<ol aria-label={t.wizard.stepsLabel} className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-[12.5px]">
			{STEPS.map((s, i) => (
				<li
					key={s}
					aria-current={s === current ? "step" : undefined}
					className={cn(
						"font-medium text-muted-foreground",
						i < at && "text-foreground-2",
						s === current && "font-bold text-foreground",
					)}
				>
					{i + 1}. {t.wizard.steps[s]}
				</li>
			))}
		</ol>
	);
}

function SessionView({
	kb,
	sid,
	onRestart,
	onTypeInstead,
}: {
	kb: string;
	sid: string;
	onRestart: () => void;
	onTypeInstead: () => void;
}) {
	const q = useQuery(setupSessionQuery(kb, sid));
	const [forced, setForced] = useState<Step | null>(null);
	const [generated, setGenerated] = useState<{ wordingUsed: "ai" | "template" } | null>(null);

	if (q.isPending) return <CardSkeleton lines={5} />;
	if (q.isError) {
		const gone = isApiError(q.error) && q.error.code === "setup_session_not_found";
		return (
			<div className="grid gap-3">
				<ErrorState error={q.error} onRetry={gone ? undefined : () => void q.refetch()} />
				<Button variant="default" className="justify-self-start" onClick={onRestart}>
					{t.wizard.startAgain}
				</Button>
			</div>
		);
	}
	const session = q.data;
	if (session.status === "abandoned") {
		return (
			<div className="grid gap-3 rounded-lg border border-border bg-card p-5">
				<h2 className="m-0 text-lg font-semibold">{t.wizard.abandonedTitle}</h2>
				<p className="m-0 text-[13px] text-muted-foreground">{t.wizard.abandonedBody}</p>
				<Button variant="default" className="justify-self-start" onClick={onRestart}>
					{t.wizard.startAgain}
				</Button>
			</div>
		);
	}
	const step = stepOf(session, forced);

	return (
		<>
			<StepList current={step} />
			{session.recommend_multilingual ? <MultilingualNote kb={kb} /> : null}
			{step === "website" ? (
				<CrawlStep kb={kb} sid={sid} url={session.website_url ?? ""} onTypeInstead={onTypeInstead} />
			) : step === "confirm" ? (
				<ProposalStep kb={kb} session={session} />
			) : step === "questions" ? (
				<InterviewStep
					kb={kb}
					session={session}
					onGenerated={(r) => {
						setGenerated({ wordingUsed: r.wordingUsed });
						setForced(null);
					}}
				/>
			) : step === "review" ? (
				<ReviewStep
					kb={kb}
					session={session}
					wordingUsed={generated?.wordingUsed ?? null}
					onChangeAnswers={() => setForced("questions")}
				/>
			) : step === "checks" ? (
				<TestsStep kb={kb} session={session} onContinue={() => setForced("publish")} />
			) : (
				<PublishStep
					kb={kb}
					session={session}
					version={null}
					onBack={() => setForced(session.eval_run_id ? "checks" : "review")}
				/>
			)}
		</>
	);
}

/** Hindi or Hinglish was chosen but the search model is English-only: say so, from the catalog flag. */
function MultilingualNote({ kb }: { kb: string }) {
	const catalog = useQuery(embeddingCatalogQuery);
	const names = (catalog.data ?? [])
		.filter((e) => (e as { multilingual?: boolean }).multilingual === true)
		.map((e) => e.name);
	return (
		<div role="status" className="grid gap-1 rounded-md bg-warning-soft p-3 text-[13px] text-warning">
			<span>{t.wizard.multilingual}</span>
			{names.length ? <span>{fill(t.wizard.multilingualNames, { names: names.join(", ") })}</span> : null}
			<Link to="/kbs/$kbId" params={{ kbId: kb }} search={{ tab: "embedding" }} className="font-semibold underline">
				{t.wizard.multilingualLink}
			</Link>
		</div>
	);
}
