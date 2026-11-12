import { toast } from "sonner";
import { CopyButton } from "@/components/patterns/CopyButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/format/date";
import { Markdown } from "@/lib/markdown";
import { fill } from "@/lib/strings/en";
import { leadsStrings } from "@/lib/strings/leads";
import { formatPhone } from "./format";
import { HandoffStatusBadge } from "./HandoffsTable";
import { type HandoffDetail, useHandoff, useUpdateHandoff } from "./handoffs-api";

const t = leadsStrings.handoffs;
const d = t.drawer;

function Row({ label, children }: { label: string; children: React.ReactNode }) {
	return (
		<div className="grid gap-0.5 min-[520px]:grid-cols-[130px_1fr] min-[520px]:gap-3">
			<dt className="text-[12.5px] text-muted-foreground">{label}</dt>
			<dd className="m-0 break-words">{children}</dd>
		</div>
	);
}

function Body({ h }: { h: HandoffDetail }) {
	const update = useUpdateHandoff();
	const next = h.status === "new" ? "handled" : "new";
	const toggle = () =>
		update.mutate(
			{ id: h.id, status: next },
			{
				onSuccess: () => toast.success(next === "handled" ? d.markedHandled : d.markedNew),
				onError: (e) => toast.error(messageFor(toApiError(e)).title),
			},
		);
	const lead = h.lead;
	const digits = lead?.phone?.replace(/\D/g, "");
	return (
		<div className="grid min-h-0 flex-1 content-start gap-5 overflow-y-auto px-4 pb-4">
			<div className="flex flex-wrap items-center gap-2">
				<HandoffStatusBadge status={h.status} />
				<span className="text-[13px] text-muted-foreground">
					{t.channels[h.channel] ?? h.channel} · {t.reasons[h.reason] ?? h.reason_label}
				</span>
			</div>
			<section aria-labelledby="handoff-summary" className="grid gap-1.5">
				<h3 id="handoff-summary" className="text-[13px] font-semibold">
					{d.summary}
				</h3>
				<p className="m-0 whitespace-pre-line text-[13.5px] text-foreground-2">{h.summary || d.noSummary}</p>
				<dl className="m-0 mt-1 grid gap-1 text-[12.5px] text-muted-foreground">
					{/* TV2 reference */}
					{h.ref_code ? (
						<Row label={leadsStrings.reference.label}>
							<span className="inline-flex flex-wrap items-center gap-2">
								<span className="font-mono text-foreground">{h.ref_code}</span>
								<CopyButton value={h.ref_code} ariaLabel={leadsStrings.reference.copy} />
							</span>
						</Row>
					) : null}
					{h.page_url ? <Row label={d.page}>{h.page_url}</Row> : null}
					{h.office_open != null ? <Row label={d.office}>{h.office_open ? d.officeOpen : d.officeClosed}</Row> : null}
				</dl>
			</section>
			{lead ? (
				<section aria-labelledby="handoff-visitor" className="grid gap-2">
					<h3 id="handoff-visitor" className="text-[13px] font-semibold">
						{d.visitor}
					</h3>
					<dl className="m-0 grid gap-1.5 text-[13.5px]">
						<Row label={d.name}>{lead.name || d.noName}</Row>
						{lead.phone ? (
							<Row label={d.phone}>
								<span className="inline-flex flex-wrap items-center gap-2">
									<a href={`tel:${lead.phone}`} className="underline-offset-4 hover:underline">
										{formatPhone(lead.phone)}
									</a>
									<a
										href={`https://wa.me/${digits}`}
										target="_blank"
										rel="noopener noreferrer"
										className="underline-offset-4 hover:underline"
									>
										{fill(d.whatsapp, { who: lead.name || formatPhone(lead.phone) })}
									</a>
									<CopyButton value={lead.phone} ariaLabel={d.copyPhone} />
								</span>
							</Row>
						) : null}
						{lead.email ? (
							<Row label={d.email}>
								<a href={`mailto:${lead.email}`} className="underline-offset-4 hover:underline">
									{lead.email}
								</a>
							</Row>
						) : null}
					</dl>
				</section>
			) : null}
			<section aria-labelledby="handoff-chat" className="grid gap-2">
				<h3 id="handoff-chat" className="text-[13px] font-semibold">
					{d.transcript}
				</h3>
				{(h.transcript ?? []).length === 0 ? (
					<p className="m-0 text-[13px] text-muted-foreground">{d.transcriptEmpty}</p>
				) : (
					<ol className="m-0 grid list-none gap-2 p-0">
						{(h.transcript ?? []).map((m) => (
							<li
								key={`${m.created_at}-${m.role}-${m.content.slice(0, 24)}`}
								className="grid gap-0.5 rounded-md border border-border bg-card px-3 py-2 text-[13.5px]"
							>
								<span className="font-mono text-[10.5px] tracking-[.08em] text-muted-foreground uppercase">
									{m.role === "user" ? d.visitorRole : d.assistantRole}
								</span>
								{m.role === "user" ? (
									<p className="m-0 whitespace-pre-wrap">{m.content}</p>
								) : (
									<Markdown text={m.content} sources={[]} copyable={false} />
								)}
							</li>
						))}
					</ol>
				)}
			</section>
			<div>
				<Button variant={h.status === "new" ? "default" : "outline"} onClick={toggle} disabled={update.isPending}>
					{h.status === "new" ? d.markHandled : d.markNew}
				</Button>
			</div>
		</div>
	);
}

/** One handoff beside the list: why, the summary, who the visitor is, the chat, and "Mark handled". */
export function HandoffDrawer({ id, onClose }: { id: string | undefined; onClose: () => void }) {
	const q = useHandoff(id);
	return (
		<Sheet open={!!id} onOpenChange={(o) => (o ? undefined : onClose())}>
			<SheetContent className="w-full sm:max-w-xl" aria-describedby="handoff-drawer-description">
				<SheetHeader>
					<SheetTitle>
						{q.data ? fill(d.title, { when: formatDateTime(q.data.created_at) }) : d.fallbackTitle}
					</SheetTitle>
					<SheetDescription id="handoff-drawer-description">{d.description}</SheetDescription>
				</SheetHeader>
				{q.isPending ? (
					<div className="px-4">
						<CardSkeleton lines={4} />
					</div>
				) : q.isError ? (
					<div className="px-4">
						<ErrorState error={q.error} title={d.load} onRetry={() => void q.refetch()} />
					</div>
				) : (
					<Body h={q.data} />
				)}
			</SheetContent>
		</Sheet>
	);
}
