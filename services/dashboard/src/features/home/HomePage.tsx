import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Bot, Contact, MessageSquare, PenLine, PhoneCall, Plus, Sparkles } from "lucide-react";
import { useEffect, useState } from "react"; // BIZ4
import { toast } from "sonner";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { meterText } from "@/components/shell/ConsumptionChip";
import { Button, buttonVariants } from "@/components/ui/button";
import { FairUseHomeTile } from "@/features/fair-use/HomeTile"; // PIP4
import { planMeQuery } from "@/features/kbs/api";
import { Panel } from "@/features/kbs/ui";
import { TryDialog } from "@/features/onboarding/TryDialog"; // BIZ4
import { greetingKey } from "@/features/overview/kpis";
import { planLines } from "@/features/overview/YourKbsCard";
import { consumptionQuery } from "@/features/usage/consumption";
import { ValueHomeCard } from "@/features/value/ValueHomeCard"; // TV4
import { useWidgetKeys } from "@/features/widget/api";
import type { BillingMeter } from "@/lib/api/billing-types";
import { firstName } from "@/lib/auth/principal";
import { usePrincipal } from "@/lib/auth/store";
import { avatarSrc } from "@/lib/avatar"; // BIZ4
import { useBranding } from "@/lib/branding/BrandingProvider";
import { useDocumentTitle } from "@/lib/branding/useDocumentTitle";
import { openCob } from "@/lib/cob-bus";
import { formatRelative } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { useMainKb } from "@/lib/main-kb";
import { fill } from "@/lib/strings/en";
import { homeStrings as t } from "@/lib/strings/home";
import { onboardingStrings as ob } from "@/lib/strings/onboarding"; // BIZ4
import { cn } from "@/lib/utils";
import { type Home, type HomeSite, homeQuery } from "./api";
import { Nudges } from "./Nudges"; // TV5

/** `/` for a business: is the assistant live, what it knows, today's numbers and what to do next. */
export function HomePage() {
	const principal = usePrincipal();
	const { productName } = useBranding();
	const main = useMainKb();
	useDocumentTitle(t.title);
	// The engine picks the main knowledge base itself; naming it keeps this page and the others in step.
	const home = useQuery({ ...homeQuery(main.kbId ?? undefined), enabled: !main.isLoading });
	const name = principal ? firstName(principal) : "";
	const greeting = fill(t.greeting[greetingKey(new Date().getHours())], { name });

	const header = (
		<div className="min-w-0">
			<h1 tabIndex={-1} className="font-mono text-[11px] font-medium tracking-[.08em] text-muted-foreground uppercase">
				{t.title}
			</h1>
			<p data-testid="greeting" className="mt-1 mb-0 font-display text-2xl font-bold break-words">
				{greeting}
			</p>
			<p className="mt-1 mb-0 text-foreground-2">{t.lead}</p>
		</div>
	);

	if (main.isLoading || home.isPending) {
		return (
			<div className="grid gap-4" aria-busy="true">
				{header}
				<CardSkeleton lines={3} />
				<CardSkeleton lines={3} />
			</div>
		);
	}
	if (home.isError) {
		return (
			<div className="grid gap-4">
				{header}
				<ErrorState error={home.error} onRetry={() => void home.refetch()} />
			</div>
		);
	}
	const data = home.data;
	// BIZ4 onboarding: a new owner with nothing set up goes to the first-run steps once
	if (!data.kb_id && data.onboarding?.status === "new" && principal?.role === "owner" && !redirectedBefore()) {
		return <GoToWelcome />;
	}
	// /BIZ4 onboarding
	if (!data.kb_id) {
		return (
			<div className="grid gap-4">
				{header}
				<EmptyState
					title={t.noKb.title}
					body={fill(t.noKb.body, { product: productName })}
					primaryAction={
						<Link to="/kbs" search={{ new: 1 }} className={buttonVariants({ variant: "default" })}>
							<Plus aria-hidden />
							{t.noKb.action}
						</Link>
					}
				/>
			</div>
		);
	}
	return (
		<div className="grid gap-4">
			{header}
			<SetupCard home={data} owner={principal?.role === "owner"} />
			{/* TV5: next steps, for the owner */}
			{principal?.role === "owner" && data.nudges?.length ? <Nudges nudges={data.nudges} /> : null}
			{/* /TV5 */}
			<div className="grid items-start gap-4 min-[1001px]:grid-cols-2">
				<StatusCard home={data} />
				<TodayCard home={data} />
				{/* PIP4: only when a limit is close or passed */}
				{data.fair_use ? <FairUseHomeTile tile={data.fair_use} owner={principal?.role === "owner"} /> : null}
				{/* /PIP4 */}
				{/* TV4: the value card; absent when the provider switched the value report off */}
				{data.value ? <ValueHomeCard value={data.value} owner={principal?.role === "owner"} /> : null}
				{/* /TV4 */}
				<PlanCard />
				<ActionsCard kbId={data.kb_id} assistant={data.assistant} />
			</div>
			<TipsCard />
		</div>
	);
}

// ------------------------------------------------------------------ status

function siteLine(site: HomeSite): string {
	if (site.status === "failed" || site.status === "blocked") return t.status.website.failed;
	if (site.status === "paused") return t.status.website.paused;
	if (site.status === "discovering" || site.status === "learning") return t.status.website.learning;
	const pages =
		site.pages_learned === 1
			? t.status.website.learnedOne
			: fill(t.status.website.learned, { pages: formatNumber(site.pages_learned) });
	return site.last_updated_at
		? `${pages} · ${fill(t.status.website.updated, { when: formatRelative(site.last_updated_at) })}`
		: pages;
}

function StatusCard({ home }: { home: Home }) {
	const a = home.assistant;
	const label = a.name ?? t.status.unnamed;
	const headline = !a.published
		? t.status.notPublished
		: a.enabled
			? fill(t.status.live, { name: label })
			: fill(t.status.off, { name: label });
	const tone = a.published && a.enabled ? "live" : "idle";
	return (
		<Panel title={t.status.title} className="min-[1001px]:row-span-1">
			<div className="grid gap-4 p-4">
				<div className="flex items-start gap-3">
					<Avatar url={a.avatar_url} />
					<div className="min-w-0">
						<div
							data-testid="assistant-status"
							data-state={tone}
							className="flex items-center gap-2 font-semibold break-words"
						>
							<span
								aria-hidden
								className={cn("size-2.5 shrink-0 rounded-full", tone === "live" ? "bg-success" : "bg-muted-foreground")}
							/>
							{headline}
						</div>
						{a.business_name ? (
							<div className="text-[12.5px] text-muted-foreground">
								{fill(t.status.forBusiness, { business: a.business_name })}
							</div>
						) : null}
						{!a.published ? (
							<div className="mt-1 text-[13px] text-foreground-2">{t.status.notPublishedHint}</div>
						) : null}
					</div>
				</div>
				<div className="grid gap-1.5 border-t border-border pt-3" data-testid="website-status">
					{home.website.sites.length === 0 ? (
						<div className="text-[13px]">
							<div className="font-semibold">
								{home.website.learning ? t.status.website.none : t.status.website.off}
							</div>
							{home.website.learning ? <div className="text-muted-foreground">{t.status.website.noneHint}</div> : null}
						</div>
					) : (
						home.website.sites.map((s) => (
							<div key={s.origin} className="text-[13px]">
								<div className="font-semibold break-all">{s.origin.replace(/^https?:\/\//, "")}</div>
								<div className="text-muted-foreground">{siteLine(s)}</div>
							</div>
						))
					)}
				</div>
				{/* BIZ4 onboarding: has the assistant loaded on the website yet */}
				{home.website.sites.length > 0 ? (
					<div
						data-testid="site-connected"
						data-state={home.website.connected ? "connected" : "waiting"}
						className="flex items-center gap-2 text-[13px]"
					>
						<span
							aria-hidden
							className={cn(
								"size-2.5 shrink-0 rounded-full",
								home.website.connected ? "bg-success" : "bg-muted-foreground",
							)}
						/>
						{home.website.connected ? ob.home.connected : ob.home.notConnected}
					</div>
				) : null}
				{/* /BIZ4 onboarding */}
				<div className="flex flex-wrap gap-2">
					<Link
						to="/assistant"
						className={buttonVariants({ variant: a.published ? "outline" : "default", size: "sm" })}
					>
						<Bot aria-hidden />
						{a.published ? t.status.openAssistant : t.status.publishNow}
					</Link>
					{home.website.learning ? (
						<Link
							to="/knowledge"
							search={{ tab: "website" }}
							className={buttonVariants({ variant: "outline", size: "sm" })}
						>
							{home.website.sites.length === 0 ? t.status.website.add : t.status.website.open}
						</Link>
					) : null}
				</div>
			</div>
		</Panel>
	);
}

function Avatar({ url }: { url: string | null }) {
	// A picture the owner chose; otherwise a plain bot mark. Never a broken image.
	const src = avatarSrc(url); // BIZ4: an uploaded picture is the engine's own path
	if (src) {
		return <img src={src} alt="" className="size-11 shrink-0 rounded-full border border-border object-cover" />;
	}
	return (
		<span
			aria-hidden
			className="grid size-11 shrink-0 place-items-center rounded-full bg-secondary text-muted-foreground"
		>
			<Bot className="size-5" />
		</span>
	);
}

// ------------------------------------------------------------------- today

function TodayCard({ home }: { home: Home }) {
	const d = home.today;
	return (
		<Panel title={t.today.title} sub={d.date}>
			<div className="grid grid-cols-3 gap-px bg-border">
				<Stat
					testId="today-conversations"
					icon={MessageSquare}
					value={d.conversations}
					label={t.today.conversations}
					to="/conversations"
				/>
				<Stat testId="today-leads" icon={Contact} value={d.leads} label={t.today.leads} to="/leads" />
				<Stat testId="today-waiting" icon={PhoneCall} value={d.handoffs_open} label={t.today.waiting} to="/leads" />
			</div>
		</Panel>
	);
}

function Stat({
	icon: Icon,
	value,
	label,
	to,
	testId,
}: {
	icon: typeof Bot;
	value: number;
	label: string;
	to: "/conversations" | "/leads";
	testId: string;
}) {
	return (
		<Link
			to={to}
			data-testid={testId}
			className="grid content-start gap-1 bg-card p-4 text-foreground no-underline hover:bg-accent"
		>
			<Icon className="size-4 text-muted-foreground" aria-hidden />
			<span className="font-display text-2xl font-bold">{formatNumber(value)}</span>
			<span className="text-[12.5px] text-muted-foreground">{label}</span>
		</Link>
	);
}

// -------------------------------------------------------------------- plan

function PlanCard() {
	const plan = useQuery(planMeQuery);
	const consumption = useQuery(consumptionQuery);
	const lines = plan.data ? planLines(plan.data) : null;
	return (
		<Panel
			title={t.plan.title}
			aside={
				<Link to="/plan" className={buttonVariants({ variant: "ghost", size: "sm" })}>
					{t.plan.seeMore}
				</Link>
			}
		>
			<div className="grid gap-1 p-4 text-[13.5px]" data-testid="home-plan">
				{lines ? (
					<>
						<div className="font-semibold">{lines.plan}</div>
						<div className="text-foreground-2">{lines.questions}</div>
					</>
				) : plan.isError ? (
					<div className="text-muted-foreground">{t.plan.noData}</div>
				) : (
					<CardSkeleton lines={2} />
				)}
				{consumption.data?.billing?.money_visible ? <MeterLine meter={consumption.data.billing} /> : null}
			</div>
		</Panel>
	);
}

function MeterLine({ meter }: { meter: BillingMeter }) {
	return <div className="font-semibold">{meterText(meter)}</div>;
}

// ----------------------------------------------------------------- actions

function ActionsCard({ kbId, assistant }: { kbId: string; assistant: Home["assistant"] }) {
	const keys = useWidgetKeys(kbId);
	const [trying, setTrying] = useState(false); // BIZ4: Try opens a dialog
	// A paused key, or one with no site yet, would hand over code that does nothing.
	const working =
		keys.data?.find((k) => k.is_active && k.allowed_origins.length > 0) ?? keys.data?.find((k) => k.is_active);
	const snippet = working?.embed_snippet ?? null;
	const copy = async () => {
		if (!snippet) return;
		try {
			await navigator.clipboard.writeText(snippet);
			toast.success(t.actions.copied);
		} catch {
			toast.error(t.actions.copyFailed);
		}
	};
	return (
		<Panel title={t.actions.title}>
			<ul className="m-0 grid list-none gap-px bg-border p-0">
				<Action
					icon={<Sparkles className="size-4" aria-hidden />}
					title={t.actions.copyCode}
					hint={t.actions.copyCodeHint}
					control={
						snippet ? (
							<Button size="sm" onClick={() => void copy()}>
								{t.actions.copyCode}
							</Button>
						) : (
							<Link to="/widget" search={{ kb: kbId }} className={buttonVariants({ size: "sm" })}>
								{t.actions.noCode}
							</Link>
						)
					}
				/>
				<Action
					icon={<PenLine className="size-4" aria-hidden />}
					title={t.actions.write}
					hint={t.actions.writeHint}
					control={
						<Link
							to="/knowledge"
							search={{ tab: "articles", new: "article" }}
							className={buttonVariants({ size: "sm", variant: "outline" })}
						>
							{t.actions.write}
						</Link>
					}
				/>
				<Action
					icon={<MessageSquare className="size-4" aria-hidden />}
					title={t.actions.tryIt}
					hint={t.actions.tryItHint}
					control={
						<Button size="sm" variant="outline" onClick={() => setTrying(true)}>
							{t.actions.tryIt}
						</Button>
					}
				/>
			</ul>
			<TryDialog
				kb={kbId}
				name={assistant.name}
				avatarUrl={assistant.avatar_url}
				open={trying}
				onOpenChange={setTrying}
			/>
		</Panel>
	);
}

function Action({
	icon,
	title,
	hint,
	control,
}: {
	icon: React.ReactNode;
	title: string;
	hint: string;
	control: React.ReactNode;
}) {
	return (
		<li className="flex flex-wrap items-center gap-3 bg-card px-4 py-3">
			<span aria-hidden className="text-muted-foreground">
				{icon}
			</span>
			<div className="min-w-0 flex-1">
				<div className="text-[13.5px] font-semibold">{title}</div>
				<div className="text-[12.5px] text-muted-foreground">{hint}</div>
			</div>
			{control}
		</li>
	);
}

// -------------------------------------------------------------------- tips

function TipsCard() {
	return (
		<Panel title={t.tips.title}>
			<ul className="m-0 grid list-none gap-px bg-border p-0 min-[861px]:grid-cols-3">
				{t.tips.items.map((tip) => (
					<li key={tip.title} className="grid content-between gap-3 bg-card p-4">
						<div>
							<div className="text-[13.5px] font-semibold">{tip.title}</div>
							<p className="mt-1 mb-0 text-[12.5px] text-foreground-2">{tip.body}</p>
						</div>
						<div>
							<Button
								size="sm"
								variant="outline"
								aria-label={fill(t.tips.openFor, { title: tip.title })}
								onClick={() => openCob({ prompt: tip.starter, send: false })}
							>
								{t.tips.openCob}
							</Button>
						</div>
					</li>
				))}
			</ul>
		</Panel>
	);
}

// ------------------------------------------------------------ onboarding (BIZ4)

const REDIRECT_KEY = "onboarding-redirected";

/** The redirect to /welcome happens once per browser session: skipping must never bounce back. */
function redirectedBefore(): boolean {
	try {
		return sessionStorage.getItem(REDIRECT_KEY) === "1";
	} catch {
		return false;
	}
}

function GoToWelcome() {
	const navigate = useNavigate();
	useEffect(() => {
		try {
			sessionStorage.setItem(REDIRECT_KEY, "1");
		} catch {
			// a private window: the redirect may repeat, which is harmless
		}
		void navigate({ to: "/welcome", replace: true });
	}, [navigate]);
	return <CardSkeleton lines={3} />;
}

/**
 * Where the first-run steps stand: a card to carry on while they are unfinished, a note for a
 * member (the owner is setting things up) and a quiet link to open them again afterwards.
 */
function SetupCard({ home, owner }: { home: Home; owner: boolean }) {
	const onboarding = home.onboarding;
	if (!onboarding || onboarding.status === "new") return null;
	if (onboarding.status === "in_progress") {
		if (!owner) {
			return (
				<Panel title={ob.memberTitle}>
					<p className="m-0 p-4 text-[13.5px] text-foreground-2" data-testid="setup-member">
						{ob.memberBody}
					</p>
				</Panel>
			);
		}
		return (
			<Panel title={ob.resume.title}>
				<div className="flex flex-wrap items-center justify-between gap-3 p-4" data-testid="setup-resume">
					<span className="text-[13.5px]">
						{fill(ob.resume.body, { n: String(onboarding.step), label: ob.steps[onboarding.step - 1] ?? "" })}
					</span>
					<Link to="/welcome" search={{ step: onboarding.step }} className={buttonVariants({ size: "sm" })}>
						{ob.resume.action}
					</Link>
				</div>
			</Panel>
		);
	}
	return owner ? (
		<div>
			<Link
				to="/welcome"
				search={{ step: 3 }} // TV5: the install step is step 3 now
				data-testid="setup-reopen"
				className={buttonVariants({ variant: "ghost", size: "sm" })}
			>
				{ob.home.reopen}
			</Link>
		</div>
	) : null;
}
