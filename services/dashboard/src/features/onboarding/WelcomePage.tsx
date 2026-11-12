import { useQuery } from "@tanstack/react-query";
import { getRouteApi, Link } from "@tanstack/react-router";
import { useEffect } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button, buttonVariants } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { toApiError } from "@/lib/api/errors";
import { usePrincipal } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { fill } from "@/lib/strings/en";
import { onboardingStrings as t } from "@/lib/strings/onboarding";
import { cn } from "@/lib/utils";
import { onboardingQuery, useUpdateOnboarding } from "./api";
import { BrochureStep } from "./BrochureStep"; // TV5
import { InstallStep } from "./InstallStep";
import { TryStep } from "./TryStep";
import { WebsiteStep } from "./WebsiteStep";

const route = getRouteApi("/_app/welcome");

/**
 * `/welcome?step=1|2|3|4`: a business owner's first run. Website, then brochures (optional), then the
 * code for the website, then a chat with the assistant. Skippable on every step, resumable (the engine remembers the step) and
 * open again from Home. A member sees that the owner is setting things up.
 */
export function WelcomePage() {
	const { step } = route.useSearch();
	const navigate = route.useNavigate();
	const principal = usePrincipal();
	const { productName } = useBranding();
	const query = useQuery(onboardingQuery());
	const update = useUpdateOnboarding();
	const heading = fill(t.welcome, { product: productName });
	const loaded = query.data;
	// Steps 2 to 4 need the website step done: there is no knowledge base or code before it.
	const ready = !!loaded && loaded.status !== "new" && !!loaded.kb_id && !!loaded.widget_key_id;
	const start = !loaded ? undefined : ready ? loaded.step : 1;
	// The address always names the step. Without `?step` the engine's own step is taken once and
	// written into the address, so a later change of the engine's step (step 1 finishing) never
	// moves the owner on by itself: the step they are on is the one in the address.
	useEffect(() => {
		if (step === undefined && start !== undefined) void navigate({ search: { step: start }, replace: true });
	}, [step, start, navigate]);

	const leave = () => void navigate({ to: "/" });
	const skip = () => update.mutate({ status: "dismissed" }, { onSuccess: leave });
	const header = (
		<PageHeader
			title={heading}
			description={t.lead}
			actions={
				principal?.role === "member" ? null : (
					<Button variant="ghost" onClick={skip} disabled={update.isPending}>
						{t.skip}
					</Button>
				)
			}
		/>
	);

	if (principal?.role === "member") {
		return (
			<div className="mx-auto grid w-full max-w-[820px] gap-4">
				<PageHeader title={heading} />
				<Panel title={t.memberTitle}>
					<div className="grid gap-3 p-4">
						<p className="m-0 text-[13.5px] text-foreground-2">{t.memberBody}</p>
						<div>
							<Link to="/" className={buttonVariants({ variant: "outline" })}>
								{t.memberAction}
							</Link>
						</div>
					</div>
				</Panel>
			</div>
		);
	}
	if (query.isPending) {
		return (
			<div className="mx-auto grid w-full max-w-[820px] gap-4" aria-busy="true">
				{header}
				<CardSkeleton lines={4} />
			</div>
		);
	}
	if (query.isError) {
		return (
			<div className="mx-auto grid w-full max-w-[820px] gap-4">
				{header}
				<ErrorState error={toApiError(query.error)} title={t.loadError} onRetry={() => void query.refetch()} />
			</div>
		);
	}
	const state = query.data;
	const at = ready ? Math.min(4, Math.max(1, step ?? state.step)) : 1;
	const goTo = (n: 1 | 2 | 3 | 4) => {
		if (state.status !== "new") update.mutate({ step: n });
		void navigate({ search: { step: n } });
	};
	return (
		<div className="mx-auto grid w-full max-w-[820px] gap-4">
			{header}
			<ol aria-label={t.stepsLabel} className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-[12.5px]">
				{t.steps.map((label, i) => (
					<li
						key={label}
						aria-current={i + 1 === at ? "step" : undefined}
						className={cn("font-medium text-muted-foreground", i + 1 === at && "font-bold text-foreground")}
					>
						{i + 1}. {label}
					</li>
				))}
			</ol>
			<Panel title={fill(t.stepOf, { n: String(at) })} sub={t.steps[at - 1]}>
				<div className="p-4">
					{at === 1 ? <WebsiteStep state={state} onDone={() => goTo(2)} /> : null}
					{at === 2 ? <BrochureStep state={state} onNext={() => goTo(3)} onBack={() => goTo(1)} /> : null}
					{at === 3 ? <InstallStep state={state} onNext={() => goTo(4)} onBack={() => goTo(2)} /> : null}
					{at === 4 ? (
						<TryStep
							state={state}
							onBack={() => goTo(3)}
							finishing={update.isPending}
							onFinish={() => update.mutate({ status: "done" }, { onSuccess: leave })}
						/>
					) : null}
				</div>
			</Panel>
		</div>
	);
}
