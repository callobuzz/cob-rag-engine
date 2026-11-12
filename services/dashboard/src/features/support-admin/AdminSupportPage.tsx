import { useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { useId, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Panel } from "@/features/kbs/ui";
import { RequestStatus } from "@/features/support/RequestStatus";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatDateTime, formatRelative } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { supportStrings } from "@/lib/strings/support";
import { cn } from "@/lib/utils";
import {
	type AdminSupportRequest,
	adminSupportListQuery,
	adminSupportQuery,
	formatWait,
	type ReplyStatus,
	type SupportStatus,
	supportStatsQuery,
	useReplyToSupport,
	useSetSupportStatus,
} from "./api";

const t = supportStrings.admin;
const route = getRouteApi("/_app/admin/support");

/** The numbers above the inbox: waiting for you, median first reply, share within the promise. */
function StatsStrip() {
	const q = useQuery(supportStatsQuery);
	const s = q.data;
	const cell = (label: string, value: string, testid: string) => (
		<div className="grid gap-0.5 rounded-lg border border-border bg-card px-4 py-3">
			<span className="text-[12.5px] text-muted-foreground">{label}</span>
			<strong data-testid={testid} className="text-[20px] font-bold tabular-nums">
				{value}
			</strong>
		</div>
	);
	return (
		<section aria-label={t.title} data-testid="support-stats" className="grid gap-3 min-[700px]:grid-cols-3">
			{cell(t.stats.open, s ? String(s.open) : t.stats.none, "stat-open")}
			{cell(t.stats.median, s ? formatWait(s.median_first_response_minutes, t.stats) : t.stats.none, "stat-median")}
			{cell(
				s?.sla_hours ? fill(t.stats.sla, { hours: s.sla_hours }) : t.stats.slaNone,
				s?.within_sla_pct != null ? `${s.within_sla_pct}%` : t.stats.none,
				"stat-sla",
			)}
		</section>
	);
}

// LP3 landing
type Visitor = { name?: string; email?: string | null; phone?: string | null };

/** A request written on the public website's contact page: who, and how to write back. */
export function visitorOf(context: Record<string, unknown>): Visitor | null {
	const found = context.landing_contact;
	return found && typeof found === "object" ? (found as Visitor) : null;
}

export function whatsappLink(phone: string): string | null {
	const digits = phone.replace(/\D/g, "");
	const full = digits.length === 10 ? `91${digits}` : digits;
	return full.length >= 10 && full.length <= 15 ? `https://wa.me/${full}` : null;
}

export function WebsiteBadge() {
	return (
		<span
			data-testid="website-badge"
			className="rounded-full border border-border bg-secondary px-2 py-0.5 text-[11.5px] font-semibold text-foreground-2"
		>
			{t.website.badge}
		</span>
	);
}

export function VisitorLinks({ visitor }: { visitor: Visitor }) {
	const wa = visitor.phone ? whatsappLink(visitor.phone) : null;
	return (
		<div
			data-testid="visitor-links"
			className="grid gap-1 rounded-lg border border-border bg-secondary p-3 text-[13.5px]"
		>
			<strong>
				{t.website.visitor}: {visitor.name}
			</strong>
			<div className="flex flex-wrap gap-x-4 gap-y-1">
				{visitor.email ? (
					<a href={`mailto:${visitor.email}`}>
						{t.website.email}: {visitor.email}
					</a>
				) : null}
				{visitor.phone ? (
					<a href={`tel:${visitor.phone.replace(/[^\d+]/g, "")}`}>
						{t.website.phone}: {visitor.phone}
					</a>
				) : null}
				{wa ? (
					<a href={wa} target="_blank" rel="noopener noreferrer">
						{t.website.whatsapp}
					</a>
				) : null}
			</div>
		</div>
	);
}
// /LP3 landing

function ContextList({ context }: { context: Record<string, unknown> }) {
	const labels = t.contextLabels as Record<string, string>;
	const rows = Object.entries(context).filter(([k]) => k in labels);
	if (rows.length === 0) return null;
	return (
		<dl
			data-testid="support-context"
			className="m-0 grid gap-1 rounded-lg border border-border bg-secondary p-3 text-[13px]"
		>
			<dt className="font-bold">{t.context}</dt>
			{rows.map(([k, v]) => (
				<div key={k} className="flex flex-wrap justify-between gap-3">
					<dt className="text-muted-foreground">{labels[k]}</dt>
					<dd className="m-0 font-mono">
						{typeof v === "boolean" ? (v ? t.yes : t.no) : Array.isArray(v) ? v.join(", ") || "—" : String(v ?? "—")}
					</dd>
				</div>
			))}
		</dl>
	);
}

function Thread({ id }: { id: string }) {
	const field = useId();
	const q = useQuery(adminSupportQuery(id));
	const reply = useReplyToSupport(id);
	const setStatus = useSetSupportStatus(id);
	const [text, setText] = useState("");
	const [after, setAfter] = useState<ReplyStatus>("answered");
	const [failure, setFailure] = useState<string | null>(null);
	if (q.isPending) return <CardSkeleton lines={5} />;
	if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
	const r = q.data;

	const send = async (e: React.FormEvent) => {
		e.preventDefault();
		setFailure(null);
		if (text.trim() === "") return;
		try {
			await reply.mutateAsync({ body: text, status: after });
			setText("");
			toast.success(t.sent);
		} catch (err) {
			setFailure(messageFor(toApiError(err)).title);
		}
	};
	const change = async (status: SupportStatus) => {
		setFailure(null);
		try {
			await setStatus.mutateAsync(status);
			toast.success(t.statusChanged);
		} catch (err) {
			setFailure(messageFor(toApiError(err)).title);
		}
	};

	return (
		<div data-testid="admin-support-thread" className="grid gap-4 p-4">
			<div className="flex flex-wrap items-start justify-between gap-3">
				<div className="min-w-0">
					<h2 className="text-[16px] font-bold break-words">{r.subject}</h2>
					<p className="m-0 text-[12.5px] text-muted-foreground">
						#{r.number} · {r.workspace_name} · {fill(t.from, { email: r.created_by_email })}
					</p>
				</div>
				<div className="flex flex-wrap items-center gap-2">
					{/* LP3 landing */}
					{visitorOf(r.context) ? <WebsiteBadge /> : null}
					{/* /LP3 landing */}
					<RequestStatus status={r.status} short />
					<NativeSelect
						aria-label={t.setStatus}
						value={r.status}
						disabled={setStatus.isPending}
						onChange={(e) => void change(e.target.value as SupportStatus)}
					>
						{(["open", "answered", "waiting", "closed"] as const).map((s) => (
							<option key={s} value={s}>
								{supportStrings.statusShort[s]}
							</option>
						))}
					</NativeSelect>
				</div>
			</div>
			{/* LP3 landing */}
			{visitorOf(r.context) ? <VisitorLinks visitor={visitorOf(r.context) as Visitor} /> : null}
			{/* /LP3 landing */}
			<ContextList context={r.context} />
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
							<strong className="text-foreground">{m.author_email}</strong>
							<time dateTime={m.created_at}>{formatDateTime(m.created_at)}</time>
						</div>
						<p className="m-0 text-[13.5px] whitespace-pre-wrap break-words">{m.body}</p>
					</li>
				))}
			</ol>
			<form onSubmit={(e) => void send(e)} noValidate className="grid gap-3">
				{failure ? <FormAlert>{failure}</FormAlert> : null}
				{/* LP3 landing */}
				{visitorOf(r.context) ? <p className="m-0 text-[13px] text-muted-foreground">{t.website.replyNote}</p> : null}
				{/* /LP3 landing */}
				<Field id={field} label={t.reply}>
					<Textarea id={field} rows={5} value={text} onChange={(e) => setText(e.target.value)} />
				</Field>
				<div className="flex flex-wrap items-end gap-3">
					<Field id={`${field}-after`} label={t.replyStatus}>
						<NativeSelect id={`${field}-after`} value={after} onChange={(e) => setAfter(e.target.value as ReplyStatus)}>
							{(["answered", "waiting", "closed"] as const).map((s) => (
								<option key={s} value={s}>
									{t.replyStatuses[s]}
								</option>
							))}
						</NativeSelect>
					</Field>
					<SubmitButton variant="default" pending={reply.isPending} pendingLabel={t.sending}>
						{t.send}
					</SubmitButton>
				</div>
			</form>
		</div>
	);
}

function Row({
	r,
	active,
	status,
	q,
}: {
	r: AdminSupportRequest;
	active: boolean;
	status?: SupportStatus;
	q?: string;
}) {
	return (
		<li className="border-b border-border last:border-b-0">
			<Link
				to="/admin/support"
				search={{ request: r.id, status, q }}
				data-testid="admin-support-row"
				className={cn("grid gap-1 px-4 py-3 text-foreground no-underline hover:bg-secondary", active && "bg-secondary")}
			>
				<div className="flex flex-wrap items-center justify-between gap-2">
					<span className="text-[12.5px] text-muted-foreground">
						#{r.number} · {r.workspace_name}
					</span>
					{/* LP3 landing */}
					{r.source === "website" ? <WebsiteBadge /> : null}
					{/* /LP3 landing */}
					<RequestStatus status={r.status} short />
				</div>
				<span className="text-[14px] font-semibold break-words">{r.subject}</span>
				<small className="text-[12.5px] text-muted-foreground">{formatRelative(r.last_message_at)}</small>
			</Link>
		</li>
	);
}

/**
 * `/admin/support`: every business's requests, with filters, the thread, the information the
 * business sent along, a reply box that sets the next status, and first-response numbers (TV3).
 */
export function AdminSupportPage() {
	const { request, status, q } = route.useSearch();
	const navigate = useNavigate({ from: "/admin/support" });
	const list = useQuery(adminSupportListQuery(status, q));
	const [search, setSearch] = useState(q ?? "");
	return (
		<div className="grid gap-4">
			<PageHeader title={t.title} description={t.lead} />
			<StatsStrip />
			<div className="grid items-start gap-4 min-[1101px]:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
				<Panel
					title={supportStrings.list.title}
					aside={
						<NativeSelect
							aria-label={t.statusFilter}
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
							<option value="">{t.all}</option>
							{(["open", "answered", "waiting", "closed"] as const).map((s) => (
								<option key={s} value={s}>
									{supportStrings.statusShort[s]}
								</option>
							))}
						</NativeSelect>
					}
				>
					<form
						className="border-b border-border p-3"
						onSubmit={(e) => {
							e.preventDefault();
							void navigate({ search: (prev) => ({ ...prev, q: search.trim() || undefined, request: undefined }) });
						}}
					>
						<Input
							type="search"
							aria-label={t.search}
							placeholder={t.search}
							value={search}
							onChange={(e) => setSearch(e.target.value)}
						/>
					</form>
					{list.isPending ? (
						<div className="p-4" aria-busy="true">
							<CardSkeleton lines={3} />
						</div>
					) : list.isError ? (
						<div className="p-4">
							<ErrorState error={list.error} onRetry={() => void list.refetch()} />
						</div>
					) : list.data.items.length === 0 ? (
						<p data-testid="admin-support-empty" className="m-0 p-4 text-[13.5px] text-foreground-2">
							{status || q ? t.emptyFiltered : t.empty}
						</p>
					) : (
						<ul className="m-0 list-none p-0" data-testid="admin-support-list">
							{list.data.items.map((r) => (
								<Row key={r.id} r={r} active={request === r.id} status={status} q={q} />
							))}
						</ul>
					)}
				</Panel>
				<section aria-label={t.title} className="min-w-0 rounded-lg border border-border bg-card">
					{request ? (
						<Thread id={request} key={request} />
					) : (
						<p className="m-0 p-4 text-[13.5px] text-foreground-2">{supportStrings.thread.empty}</p>
					)}
				</section>
			</div>
		</div>
	);
}
