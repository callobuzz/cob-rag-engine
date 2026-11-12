import { Link2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { CopyButton } from "@/components/patterns/CopyButton";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Button } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { fill } from "@/lib/strings/en";
import { websiteStrings } from "@/lib/strings/website";
import { type LearnKeyState, useCreateLearnKey, useDeleteLearnKey } from "../api";
import { PushGuides } from "./PushGuides";

const t = websiteStrings.push;

function fail(e: unknown) {
	const m = messageFor(toApiError(e));
	toast.error(m.title, m.body ? { description: m.body } : undefined);
}

/**
 * "Update instantly when you publish": the push link. The owner makes it (shown once, copied
 * right away, never shown again: it lives in this component's state only), replaces it or
 * removes it; a member sees whether one is set.
 */
export function PushLinkCard({ kb, learnKey }: { kb: string; learnKey: LearnKeyState }) {
	const create = useCreateLearnKey(kb);
	const remove = useDeleteLearnKey(kb);
	const [fresh, setFresh] = useState<string | null>(null);
	const [confirm, setConfirm] = useState<"replace" | "remove" | null>(null);

	const make = () =>
		create.mutate(undefined, {
			onSuccess: (out) => {
				setFresh(out.push_url);
				setConfirm(null);
				toast.success(t.created);
			},
			onError: fail,
		});
	const drop = () =>
		remove.mutate(undefined, {
			onSuccess: () => {
				setFresh(null);
				setConfirm(null);
				toast.success(t.removed);
			},
			onError: fail,
		});

	return (
		<Panel title={t.title} sub={t.sub}>
			<div className="grid gap-4 p-4">
				<div className="flex flex-wrap items-center justify-between gap-3">
					<p className="m-0 flex items-center gap-2 text-[13.5px]">
						<Link2 className="size-4 text-muted-foreground" aria-hidden />
						{learnKey.set ? fill(t.isSet, { hint: learnKey.hint ?? "" }) : t.none}
					</p>
					<div className="flex gap-2">
						<OwnerGate action="website.manage">
							<Button
								size="sm"
								variant={learnKey.set ? "outline" : "default"}
								disabled={create.isPending}
								onClick={() => (learnKey.set ? setConfirm("replace") : make())}
							>
								{learnKey.set ? t.replace : t.create}
							</Button>
						</OwnerGate>
						{learnKey.set ? (
							<OwnerGate action="website.manage">
								<Button size="sm" variant="ghost" disabled={remove.isPending} onClick={() => setConfirm("remove")}>
									{t.remove}
								</Button>
							</OwnerGate>
						) : null}
					</div>
				</div>
				{fresh ? (
					<div className="grid gap-2" data-testid="push-link">
						<div className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-muted p-3">
							<div className="min-w-[180px] flex-1">
								<div className="text-xs text-muted-foreground">{t.linkLabel}</div>
								<code className="break-all">{fresh}</code>
							</div>
							<CopyButton value={fresh} ariaLabel={`Copy ${t.linkLabel}`} />
						</div>
						<p className="m-0 text-[13px] text-warning">{t.shownOnce}</p>
					</div>
				) : null}
				<PushGuides pushUrl={fresh ?? undefined} />
			</div>
			<ConfirmDialog
				open={confirm === "replace"}
				onOpenChange={(open) => !open && setConfirm(null)}
				title={t.replaceTitle}
				body={t.replaceBody}
				confirmLabel={t.replaceConfirm}
				destructive={false}
				pending={create.isPending}
				onConfirm={make}
			/>
			<ConfirmDialog
				open={confirm === "remove"}
				onOpenChange={(open) => !open && setConfirm(null)}
				title={t.removeTitle}
				body={t.removeBody}
				confirmLabel={t.removeConfirm}
				pending={remove.isPending}
				onConfirm={drop}
			/>
		</Panel>
	);
}
