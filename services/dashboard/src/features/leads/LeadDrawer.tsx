import { useState } from "react";
import { CopyButton } from "@/components/patterns/CopyButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { formatDate, formatDateTime } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { leadsStrings as t } from "@/lib/strings/leads";
import { type LeadDetail, useLead } from "./api";
import { ConsentRecord } from "./ConsentRecord";
import { DeleteLeadDialog } from "./DeleteLeadDialog";
import { formatPhone, leadLabel } from "./format";
import { LeadEditForm } from "./LeadEditForm";
import { LeadStatusBadge, QualificationBadge } from "./LeadsTable";
import { LeadTranscript } from "./LeadTranscript";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
	return (
		<div className="grid gap-0.5 min-[520px]:grid-cols-[130px_1fr] min-[520px]:gap-3">
			<dt className="text-[12.5px] text-muted-foreground">{label}</dt>
			<dd className="m-0 break-words">{children}</dd>
		</div>
	);
}

function Body({ lead, onDeleted }: { lead: LeadDetail; onDeleted: () => void }) {
	const [confirming, setConfirming] = useState(false);
	const who = leadLabel(lead.contact);
	const custom = Object.entries(lead.contact.custom ?? {});
	const utm = Object.entries(lead.utm ?? {});
	return (
		<>
			<div className="grid min-h-0 flex-1 gap-5 overflow-y-auto px-4 pb-4">
				<div className="flex flex-wrap items-center gap-2">
					<LeadStatusBadge status={lead.status} />
					<span className="text-[13px] text-muted-foreground">
						{t.drawer.arrived} {formatDateTime(lead.created_at)} · {lead.knowledge_base}
					</span>
				</div>
				<section aria-labelledby="lead-contact" className="grid gap-2">
					<h3 id="lead-contact" className="text-[13px] font-semibold">
						{t.drawer.contact}
					</h3>
					<dl className="m-0 grid gap-1.5 text-[13.5px]">
						{lead.contact.name ? <Row label={t.drawer.name}>{lead.contact.name}</Row> : null}
						{lead.contact.phone ? (
							<Row label={t.drawer.phone}>
								<span className="inline-flex flex-wrap items-center gap-2">
									<a href={`tel:${lead.contact.phone}`} className="underline-offset-4 hover:underline">
										{formatPhone(lead.contact.phone)}
									</a>
									<CopyButton
										value={lead.contact.phone}
										ariaLabel={fill(t.drawer.copy, { what: t.drawer.phone.toLowerCase() })}
									/>
								</span>
							</Row>
						) : null}
						{lead.contact.email ? (
							<Row label={t.drawer.email}>
								<span className="inline-flex flex-wrap items-center gap-2">
									<a href={`mailto:${lead.contact.email}`} className="underline-offset-4 hover:underline">
										{lead.contact.email}
									</a>
									<CopyButton
										value={lead.contact.email}
										ariaLabel={fill(t.drawer.copy, { what: t.drawer.email.toLowerCase() })}
									/>
								</span>
							</Row>
						) : null}
						{custom.map(([k, v]) => (
							<Row key={k} label={k.replaceAll("_", " ")}>
								{String(v)}
							</Row>
						))}
					</dl>
				</section>
				<section aria-labelledby="lead-summary" className="grid gap-1.5">
					<h3 id="lead-summary" className="text-[13px] font-semibold">
						{t.drawer.summary}
					</h3>
					<p className="m-0 text-[13.5px] text-foreground-2">{lead.summary || t.drawer.noSummary}</p>
					<dl className="m-0 mt-1 grid gap-1 text-[12.5px] text-muted-foreground">
						{/* TV2 reference */}
						{lead.ref_code ? (
							<Row label={t.reference.label}>
								<span className="inline-flex flex-wrap items-center gap-2">
									<span className="font-mono text-foreground">{lead.ref_code}</span>
									<CopyButton value={lead.ref_code} ariaLabel={t.reference.copy} />
								</span>
							</Row>
						) : null}
						<Row label={t.columns.why}>{t.triggers[lead.trigger] ?? lead.trigger}</Row>
						{lead.qualification ? (
							<Row label={t.columns.qualification}>
								<QualificationBadge value={lead.qualification} />
							</Row>
						) : null}
						{lead.page_url ? <Row label={t.drawer.page}>{lead.page_url}</Row> : null}
						{utm.length ? <Row label={t.drawer.campaign}>{utm.map(([k, v]) => `${k}=${v}`).join(", ")}</Row> : null}
						{lead.sources_consulted?.length ? (
							<Row label={t.drawer.sources}>{lead.sources_consulted.join(", ")}</Row>
						) : null}
						<Row label={t.drawer.keepUntil}>{formatDate(lead.retention_until)}</Row>
					</dl>
				</section>
				<LeadEditForm key={`${lead.id}-${lead.updated_at}`} lead={lead} />
				<ConsentRecord consents={lead.consents} />
				<LeadTranscript messages={lead.transcript} />
			</div>
			<SheetFooter className="border-t border-border">
				<OwnerGate action="lead.delete">
					<Button variant="destructive" onClick={() => setConfirming(true)}>
						{t.delete.button}
					</Button>
				</OwnerGate>
			</SheetFooter>
			<DeleteLeadDialog id={lead.id} who={who} open={confirming} onOpenChange={setConfirming} onDeleted={onDeleted} />
		</>
	);
}

/** One lead beside the list: contact, what they asked, follow-up, consent, the chat, erase. */
export function LeadDrawer({ id, onClose }: { id: string | undefined; onClose: () => void }) {
	const q = useLead(id);
	const who = q.data ? leadLabel(q.data.contact) : "";
	return (
		<Sheet open={!!id} onOpenChange={(o) => (o ? undefined : onClose())}>
			<SheetContent className="w-full sm:max-w-xl" aria-describedby="lead-drawer-description">
				<SheetHeader>
					<SheetTitle>{who ? fill(t.drawer.title, { who }) : t.title}</SheetTitle>
					<SheetDescription id="lead-drawer-description">{t.drawer.description}</SheetDescription>
				</SheetHeader>
				{q.isPending ? (
					<div className="px-4">
						<CardSkeleton lines={4} />
					</div>
				) : q.isError ? (
					<div className="px-4">
						<ErrorState error={q.error} title={t.drawer.load} onRetry={() => void q.refetch()} />
					</div>
				) : (
					<Body lead={q.data} onDeleted={onClose} />
				)}
			</SheetContent>
		</Sheet>
	);
}
