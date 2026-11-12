import { useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { MessageCircleQuestion, Plus } from "lucide-react";
import { ContactBox } from "@/components/auth/ContactBox";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { openCob } from "@/lib/cob-bus";
import { formatRelative } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { supportStrings as t } from "@/lib/strings/support";
import { cn } from "@/lib/utils";
import { type SupportStatus, supportInfoQuery, supportListQuery } from "./api";
import { RequestForm } from "./RequestForm";
import { RequestStatus } from "./RequestStatus";
import { RequestThread } from "./RequestThread";

const route = getRouteApi("/_app/support");

/**
 * `/support`: write to the provider and follow the answer (TV3). Opens from the COB panel's
 * "Contact {product}" and the "Get help" button; not a menu item. The page the person came from
 * arrives as `?page=` and is sent with the request.
 */
export function SupportPage() {
	const { request, new: isNew, page, status } = route.useSearch();
	const navigate = useNavigate({ from: "/support" });
	const { productName } = useBranding();
	const info = useQuery(supportInfoQuery);
	const list = useQuery(supportListQuery(status));
	const enabled = info.data?.enabled !== false;
	const showForm = isNew === 1 && enabled;

	const header = (
		<PageHeader
			title={t.title}
			description={fill(t.lead, { product: productName })}
			actions={
				<div className="flex flex-wrap gap-2">
					<Button variant="outline" size="sm" onClick={() => openCob({})}>
						<MessageCircleQuestion aria-hidden />
						{t.cobFirst}
					</Button>
					{enabled ? (
						<Button
							variant="default"
							size="sm"
							data-testid="support-new"
							onClick={() => void navigate({ search: { new: 1, page } })}
						>
							<Plus aria-hidden />
							{t.list.new}
						</Button>
					) : null}
				</div>
			}
		/>
	);

	if (info.isError)
		return (
			<div className="grid gap-4">
				{header}
				<ErrorState error={info.error} title={t.loadError} onRetry={() => void info.refetch()} />
			</div>
		);

	return (
		<div className="grid gap-4">
			{header}
			{info.data?.sla_hours ? (
				<p data-testid="support-sla" className="m-0 text-[13.5px] text-foreground-2">
					{fill(t.sla, { hours: info.data.sla_hours })}
				</p>
			) : null}
			{info.data && !enabled ? (
				<div data-testid="support-disabled" className="grid gap-3 rounded-lg border border-border bg-card p-4">
					<h2 className="text-[15px] font-bold">{t.disabledTitle}</h2>
					<p className="m-0 text-foreground-2">{fill(t.disabled, { product: productName })}</p>
					<ContactBox />
				</div>
			) : null}
			<div className="grid items-start gap-4 min-[1101px]:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
				<Panel
					title={t.list.title}
					aside={
						<NativeSelect
							aria-label={t.list.filterLabel}
							value={status ?? ""}
							onChange={(e) =>
								void navigate({
									search: (prev) => ({
										...prev,
										status: (e.target.value || undefined) as SupportStatus | undefined,
										request: undefined,
									}),
								})
							}
						>
							<option value="">{t.list.all}</option>
							<option value="open">{t.statusShort.open}</option>
							<option value="answered">{t.statusShort.answered}</option>
							<option value="waiting">{t.statusShort.waiting}</option>
							<option value="closed">{t.statusShort.closed}</option>
						</NativeSelect>
					}
				>
					{list.isPending ? (
						<div className="p-4" aria-busy="true">
							<CardSkeleton lines={3} />
						</div>
					) : list.isError ? (
						<div className="p-4">
							<ErrorState error={list.error} title={t.loadError} onRetry={() => void list.refetch()} />
						</div>
					) : list.data.items.length === 0 ? (
						<p data-testid="support-empty" className="m-0 p-4 text-[13.5px] text-foreground-2">
							{status ? t.list.emptyFiltered : fill(t.list.empty, { product: productName })}
						</p>
					) : (
						<ul className="m-0 list-none p-0" data-testid="support-list">
							{list.data.items.map((r) => (
								<li key={r.id} className="border-b border-border last:border-b-0">
									<Link
										to="/support"
										search={{ request: r.id }}
										data-testid="support-row"
										className={cn(
											"grid gap-1 px-4 py-3 text-foreground no-underline hover:bg-secondary",
											request === r.id && "bg-secondary",
										)}
									>
										<div className="flex flex-wrap items-center justify-between gap-2">
											<span className="text-[12.5px] text-muted-foreground">
												{fill(t.list.number, { number: r.number })}
											</span>
											<RequestStatus status={r.status} short />
										</div>
										<span className="text-[14px] font-semibold break-words">{r.subject}</span>
										<small className="text-[12.5px] text-muted-foreground">
											{fill(t.list.updated, { when: formatRelative(r.last_message_at) })}
										</small>
									</Link>
								</li>
							))}
						</ul>
					)}
				</Panel>
				<section aria-label={t.title} className="min-w-0 rounded-lg border border-border bg-card">
					{showForm ? (
						<RequestForm
							page={page}
							onSent={(id) => void navigate({ search: { request: id } })}
							onCancel={() => void navigate({ search: {} })}
						/>
					) : request ? (
						<RequestThread requestId={request} key={request} />
					) : (
						<p className="m-0 p-4 text-[13.5px] text-foreground-2">{t.thread.empty}</p>
					)}
				</section>
			</div>
		</div>
	);
}
