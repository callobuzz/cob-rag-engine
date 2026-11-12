import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ErrorState } from "@/components/patterns/ErrorState";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { documentsQuery } from "@/features/documents/api";
import { Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { fill } from "@/lib/strings/en";
import { whatsappStrings as t } from "@/lib/strings/whatsapp";
import { settingsOf, useSetDocuments, type WhatsApp } from "./api";

export const MAX_SHAREABLE = 20;
const SENDABLE = [".pdf", ".jpg", ".jpeg", ".png"];
const SENDABLE_TYPES = ["application/pdf", "image/jpeg", "image/png"];

/** Whether WhatsApp can carry the file as a document: a PDF, JPG or PNG (type, else file name). */
export function isSendable(d: { file_name: string | null; mime_type: string | null }): boolean {
	if (d.mime_type && SENDABLE_TYPES.includes(d.mime_type.toLowerCase())) return true;
	const name = (d.file_name ?? "").toLowerCase();
	return SENDABLE.some((ext) => name.endsWith(ext));
}

/**
 * "Files your assistant can send" (TV6): tick up to 20 PDFs, JPGs or PNGs. When a customer asks
 * for a brochure, price list or menu, the matching ticked file is sent; nothing else is ever sent.
 */
export function DocumentsCard({ kb, data }: { kb: string; data: WhatsApp }) {
	const docs = useQuery(documentsQuery(kb, { limit: 100 }));
	const save = useSetDocuments(kb);
	const stored = settingsOf(data).shareable_document_ids;
	const [picked, setPicked] = useState<string[]>(stored);
	const storedKey = stored.join(",");
	useEffect(() => setPicked(storedKey ? storedKey.split(",") : []), [storedKey]);

	const items = (docs.data?.items ?? []).filter(isSendable);
	const toggle = (id: string, on: boolean) =>
		setPicked((cur) =>
			on ? (cur.includes(id) || cur.length >= MAX_SHAREABLE ? cur : [...cur, id]) : cur.filter((x) => x !== id),
		);
	const dirty = picked.join(",") !== stored.join(",");

	return (
		<Panel
			title={t.documents.title}
			sub={t.documents.help}
			aside={<small>{fill(t.documents.chosen, { n: picked.length })}</small>}
		>
			<div className="grid gap-3 p-4" data-testid="wa-documents">
				{docs.isPending ? (
					<CardSkeleton lines={3} />
				) : docs.isError ? (
					<ErrorState error={docs.error} title={t.documents.loadError} onRetry={() => void docs.refetch()} />
				) : items.length === 0 ? (
					<p className="m-0 text-foreground-2">{t.documents.empty}</p>
				) : (
					<ul className="m-0 grid list-none gap-1.5 p-0">
						{items.map((d) => {
							const on = picked.includes(d.id);
							const full = !on && picked.length >= MAX_SHAREABLE;
							return (
								<li key={d.id}>
									<label htmlFor={`wa-doc-${d.id}`} className="flex items-center gap-2 text-[13.5px]">
										<OwnerGate action="whatsapp.manage">
											<Checkbox
												id={`wa-doc-${d.id}`}
												checked={on}
												disabled={full}
												onCheckedChange={(v) => toggle(d.id, v === true)}
												aria-label={d.file_name ?? d.id}
											/>
										</OwnerGate>
										<span>{d.file_name ?? d.id}</span>
									</label>
								</li>
							);
						})}
					</ul>
				)}
				<small className="text-muted-foreground">{t.documents.limit}</small>
				<div>
					<OwnerGate action="whatsapp.manage">
						<Button
							variant="default"
							disabled={!dirty}
							aria-disabled={save.isPending || undefined}
							onClick={() =>
								save.mutate(picked, {
									onSuccess: () => toast.success(t.documents.saved),
									onError: (e) => toast.error(messageFor(toApiError(e)).title),
								})
							}
						>
							Save
						</Button>
					</OwnerGate>
				</div>
			</div>
		</Panel>
	);
}
