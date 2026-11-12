import { useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { SearchX } from "lucide-react";
import type { ReactNode } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { StatusBadge } from "@/components/patterns/StatusBadge";
import { PageHeader } from "@/components/shell/PageHeader";
import { buttonVariants } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { KbAvatar } from "@/features/kbs/KbAvatar";
import { AdminValueTab } from "@/features/value/AdminValueTab"; // TV4
import { isApiError } from "@/lib/api/errors";
import { useDocumentTitle } from "@/lib/branding/useDocumentTitle";
import { CUSTOMER_TABS, type CustomerTab } from "@/lib/search";
import { customersStrings as t } from "@/lib/strings/accounts";
import { cn } from "@/lib/utils";
import { tenantQuery } from "./api";
import { OperatorBadge, PlanBadge } from "./CustomerRow";
import { useCustomerActions } from "./LifecycleDialogs";
import { BillingTab } from "./tabs/Billing"; // BIL2
import { DangerTab } from "./tabs/Danger";
import { KbsTab } from "./tabs/Kbs";
import { KeysTab } from "./tabs/Keys";
import { LimitsTab } from "./tabs/Limits"; // PIP4
import { OverviewTab } from "./tabs/Overview";
import { SettingsTab } from "./tabs/Settings";
import { TeamTab } from "./tabs/Team";

const route = getRouteApi("/_app/admin/customers/$tenantId");

/** `/admin/customers/$tenantId?tab=`: Overview, Knowledge bases, Team, Keys, Settings, Danger zone. */
export function CustomerDetailPage() {
	const { tenantId } = route.useParams();
	const { tab = "overview" } = route.useSearch();
	const navigate = useNavigate();
	const q = useQuery(tenantQuery(tenantId));
	const actions = useCustomerActions({ onGone: () => void navigate({ to: "/admin/customers" }) });

	if (q.isPending) {
		return (
			<div className="grid gap-4" aria-busy="true">
				<CardSkeleton lines={2} />
				<CardSkeleton lines={5} />
			</div>
		);
	}
	if (q.isError) {
		if (isApiError(q.error) && (q.error.status === 404 || q.error.status === 422)) return <CustomerNotFound />;
		return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
	}
	const tenant = q.data;
	const panels: Record<CustomerTab, ReactNode> = {
		overview: <OverviewTab tenant={tenant} />,
		kbs: <KbsTab tenant={tenant} />,
		team: <TeamTab tenant={tenant} />,
		keys: <KeysTab tenant={tenant} />,
		billing: <BillingTab tenant={tenant} />, // BIL2
		limits: <LimitsTab tenant={tenant} />, // PIP4
		value: <AdminValueTab tenant={tenant} />, // TV4
		settings: <SettingsTab key={tenant.id + tenant.name + String(tenant.max_corpora)} tenant={tenant} />,
		danger: <DangerTab tenant={tenant} onAction={actions.run} />,
	};

	return (
		<>
			<PageHeader
				title={tenant.name}
				crumbs={[
					{ label: t.list.title, to: "/admin/customers", search: { status: tenant.status } },
					{ label: tenant.name },
				]}
				leading={<KbAvatar kb={tenant} size={40} />}
				description={
					<span className="inline-flex flex-wrap items-center gap-2">
						<StatusBadge status={tenant.status} />
						<PlanBadge tenant={tenant} />
						{tenant.is_operator ? <OperatorBadge /> : null}
						{tenant.description ? <span>{tenant.description}</span> : null}
					</span>
				}
			/>
			<Tabs
				value={tab}
				onValueChange={(v) => void navigate({ to: ".", search: (prev) => ({ ...prev, tab: v as CustomerTab }) })}
				className="min-w-0 gap-4"
			>
				<TabsList
					variant="line"
					aria-label={t.detail.tabsLabel}
					className="h-auto! w-full justify-start gap-1 overflow-x-auto overflow-y-hidden rounded-none border-b border-border p-0 [scrollbar-width:none]"
				>
					{CUSTOMER_TABS.map((k) => (
						<TabsTrigger
							key={k}
							value={k}
							className={cn(
								"h-10! flex-none rounded-none px-3 font-semibold text-foreground-2 hover:text-foreground data-[state=active]:text-foreground",
								"after:bottom-[-1px]! after:inset-x-2! after:h-0.5! after:rounded-sm after:bg-primary!",
								k === "danger" &&
									"text-destructive hover:text-destructive data-[state=active]:text-destructive dark:text-destructive dark:hover:text-destructive dark:data-[state=active]:text-destructive",
							)}
						>
							{t.detail.tabs[k]}
						</TabsTrigger>
					))}
				</TabsList>
				{CUSTOMER_TABS.map((k) => (
					<TabsContent key={k} value={k} className="min-w-0">
						{tab === k ? panels[k] : null}
					</TabsContent>
				))}
			</Tabs>
			{actions.dialogs}
		</>
	);
}

function CustomerNotFound() {
	useDocumentTitle(t.detail.notFoundTitle);
	return (
		<section
			data-testid="customer-not-found"
			className="grid justify-items-start gap-3.5 rounded-lg border border-border bg-card p-8"
		>
			<span className="grid size-[52px] place-items-center rounded-[14px] bg-secondary text-muted-foreground">
				<SearchX className="size-[26px]" aria-hidden />
			</span>
			<h1 className="text-2xl font-bold" tabIndex={-1}>
				{t.detail.notFoundTitle}
			</h1>
			<p className="m-0 max-w-[560px] text-foreground-2">{t.detail.notFoundBody}</p>
			<Link to="/admin/customers" className={buttonVariants({ variant: "outline" })}>
				{t.detail.back}
			</Link>
		</section>
	);
}
