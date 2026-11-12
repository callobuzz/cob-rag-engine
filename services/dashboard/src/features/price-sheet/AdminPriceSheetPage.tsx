import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Link2, Printer } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { CopyButton } from "@/components/patterns/CopyButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { openStatementPage } from "@/features/billing/statements/open";
import { settingsQuery, useSaveSettings } from "@/features/branding/api";
import { Panel } from "@/features/kbs/ui";
import { plansQuery } from "@/features/plans/api";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { currentRagUrl } from "@/lib/auth/store";
import { fill } from "@/lib/strings/en";
import { priceSheetStrings as t } from "@/lib/strings/price-sheet";
import { loadPriceSheetPage, move, priceSheetKeys, useRemovePriceSheetLink, useRotatePriceSheetLink } from "./api";

/** The sandboxed preview of the printable page: scripts and same-origin access are off. */
function Preview({ version }: { version: number }) {
	const [html, setHtml] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);
	// biome-ignore lint/correctness/useExhaustiveDependencies: reload when the list was saved
	useEffect(() => {
		let live = true;
		setError(null);
		loadPriceSheetPage()
			.then((page) => live && setHtml(page))
			.catch((e) => live && setError(messageFor(toApiError(e)).title));
		return () => {
			live = false;
		};
	}, [version]);
	if (error) return <FormAlert>{error}</FormAlert>;
	if (html == null) return <CardSkeleton lines={6} />;
	return (
		<iframe
			title={t.preview.frame}
			data-testid="price-sheet-preview"
			sandbox=""
			srcDoc={html}
			className="h-[560px] w-full rounded-lg border border-border bg-white"
		/>
	);
}

function LinkCard({ token }: { token: string | null | undefined }) {
	const rotate = useRotatePriceSheetLink();
	const remove = useRemovePriceSheetLink();
	const [fresh, setFresh] = useState<string | null>(null);
	const [confirm, setConfirm] = useState<"rotate" | "remove" | null>(null);
	const [error, setError] = useState<string | null>(null);
	const url = fresh ?? (token ? `${currentRagUrl().replace(/\/$/, "")}/api/v1/public/price-sheet/${token}` : null);
	const run = async (kind: "rotate" | "remove") => {
		setError(null);
		try {
			if (kind === "rotate") {
				const made = await rotate.mutateAsync();
				setFresh(made.url);
				toast.success(t.link.created);
			} else {
				await remove.mutateAsync();
				setFresh(null);
				toast.success(t.link.removed);
			}
			setConfirm(null);
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	};
	return (
		<Panel title={t.link.title} sub={t.link.lead}>
			<div data-testid="price-sheet-link" className="grid gap-3 p-4">
				{error ? <FormAlert>{error}</FormAlert> : null}
				{url ? (
					<>
						<p className="m-0 text-[12.5px] text-muted-foreground">{t.link.label}</p>
						<code data-testid="price-sheet-url" className="break-all rounded-md bg-secondary p-2 text-[12.5px]">
							{url}
						</code>
						<div className="flex flex-wrap gap-2">
							<CopyButton value={url} label={t.link.copy} ariaLabel={t.link.copy} />
							<Button variant="outline" size="sm" data-testid="price-sheet-rotate" onClick={() => setConfirm("rotate")}>
								{t.link.rotate}
							</Button>
							<Button variant="ghost" size="sm" data-testid="price-sheet-remove" onClick={() => setConfirm("remove")}>
								{t.link.remove}
							</Button>
						</div>
					</>
				) : (
					<>
						<p className="m-0 text-[13.5px] text-foreground-2">{t.link.none}</p>
						<div>
							<Button
								variant="default"
								size="sm"
								data-testid="price-sheet-create"
								disabled={rotate.isPending}
								onClick={() => void run("rotate")}
							>
								<Link2 aria-hidden />
								{t.link.create}
							</Button>
						</div>
					</>
				)}
			</div>
			<ConfirmDialog
				open={confirm !== null}
				onOpenChange={(o) => (o ? undefined : setConfirm(null))}
				title={confirm === "remove" ? t.link.confirmRemoveTitle : t.link.confirmRotateTitle}
				body={confirm === "remove" ? t.link.confirmRemoveBody : t.link.confirmRotateBody}
				confirmLabel={confirm === "remove" ? t.link.remove : t.link.rotate}
				destructive={confirm === "remove"}
				pending={rotate.isPending || remove.isPending}
				onConfirm={() => confirm && void run(confirm)}
			/>
		</Panel>
	);
}

/**
 * `/admin/price-sheet`: pick the plans and their order, show add-ons, write a note, preview and
 * print the white-label price list, and manage its public link (TV3). Operators only.
 */
export function AdminPriceSheetPage() {
	const noteId = useId();
	const qc = useQueryClient();
	const settings = useQuery(settingsQuery);
	const plans = useQuery(plansQuery);
	const save = useSaveSettings();
	const sheet = settings.data?.trust?.price_sheet;
	const [order, setOrder] = useState<string[] | null>(null);
	const [addOns, setAddOns] = useState<boolean | null>(null);
	const [note, setNote] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [version, setVersion] = useState(0);

	if (settings.isPending || plans.isPending) return <CardSkeleton lines={6} />;
	if (settings.isError) return <ErrorState error={settings.error} onRetry={() => void settings.refetch()} />;
	if (plans.isError) return <ErrorState error={plans.error} onRetry={() => void plans.refetch()} />;

	const listed = order ?? ((sheet?.plan_ids ?? []) as string[]);
	const showAddOns = addOns ?? sheet?.show_add_ons ?? true;
	const noteText = note ?? sheet?.note ?? "";
	const sellable = plans.data.items.filter((p) => p.is_active && !p.is_internal);
	const byId = new Map(sellable.map((p) => [p.id, p]));
	const unlisted = sellable.filter((p) => !listed.includes(p.id));

	const toggle = (id: string, on: boolean) => setOrder(on ? [...listed, id] : listed.filter((x) => x !== id));
	const submit = async (e: React.FormEvent) => {
		e.preventDefault();
		setError(null);
		if (noteText.length > 500) return setError(t.options.noteLong);
		try {
			await save.mutateAsync({
				trust: { price_sheet: { plan_ids: listed, show_add_ons: showAddOns, note: noteText } },
			});
			await qc.invalidateQueries({ queryKey: priceSheetKeys.all });
			setVersion((v) => v + 1);
			setOrder(null);
			setAddOns(null);
			setNote(null);
			toast.success(t.options.saved);
		} catch (err) {
			setError(messageFor(toApiError(err)).title);
		}
	};
	const print = async () => {
		try {
			const opened = await openStatementPage(loadPriceSheetPage, { print: true });
			if (!opened) toast.error(t.preview.blocked);
		} catch (e) {
			toast.error(t.preview.printFailed, { description: messageFor(toApiError(e)).title });
		}
	};

	return (
		<div className="grid gap-4">
			<PageHeader
				title={t.title}
				description={t.lead}
				actions={
					<Button variant="outline" size="sm" data-testid="price-sheet-print" onClick={() => void print()}>
						<Printer aria-hidden />
						{t.preview.print}
					</Button>
				}
			/>
			<div className="grid items-start gap-4 min-[1101px]:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
				<div className="grid gap-4">
					<form onSubmit={(e) => void submit(e)} noValidate>
						<Panel title={t.plans.title} sub={t.plans.lead}>
							<div className="grid gap-3 p-4">
								{error ? <FormAlert>{error}</FormAlert> : null}
								<ol className="m-0 grid list-none gap-1.5 p-0" data-testid="price-sheet-plans">
									{listed.map((id, i) => {
										const p = byId.get(id);
										if (!p) return null;
										return (
											<li key={id} className="flex items-center gap-2 rounded-md border border-border px-3 py-2">
												<Switch
													size="sm"
													checked
													aria-label={fill(t.plans.show, { name: p.name })}
													onCheckedChange={() => toggle(id, false)}
												/>
												<span className="min-w-0 flex-1 text-[13.5px] font-semibold break-words">{p.name}</span>
												<Button
													type="button"
													variant="ghost"
													size="icon"
													disabled={i === 0}
													aria-label={fill(t.plans.moveUp, { name: p.name })}
													onClick={() => setOrder(move(listed, id, -1))}
												>
													<ArrowUp aria-hidden />
												</Button>
												<Button
													type="button"
													variant="ghost"
													size="icon"
													disabled={i === listed.length - 1}
													aria-label={fill(t.plans.moveDown, { name: p.name })}
													onClick={() => setOrder(move(listed, id, 1))}
												>
													<ArrowDown aria-hidden />
												</Button>
											</li>
										);
									})}
									{unlisted.map((p) => (
										<li
											key={p.id}
											className="flex items-center gap-2 rounded-md border border-dashed border-border px-3 py-2"
										>
											<Switch
												size="sm"
												checked={false}
												aria-label={fill(t.plans.show, { name: p.name })}
												onCheckedChange={() => toggle(p.id, true)}
											/>
											<span className="min-w-0 flex-1 text-[13.5px] break-words text-foreground-2">{p.name}</span>
										</li>
									))}
									{sellable.length === 0 ? <li className="text-[13.5px] text-foreground-2">{t.plans.empty}</li> : null}
								</ol>
								<div className="flex items-center gap-2.5">
									<Switch id={`${noteId}-addons`} checked={showAddOns} onCheckedChange={setAddOns} />
									<label htmlFor={`${noteId}-addons`} className="text-[13.5px] font-semibold">
										{t.options.addOns}
									</label>
								</div>
								<Field id={noteId} label={t.options.note} hint={t.options.noteHint}>
									<Textarea
										id={noteId}
										rows={3}
										maxLength={600}
										value={noteText}
										aria-describedby={`${noteId}-hint`}
										onChange={(e) => setNote(e.target.value)}
									/>
								</Field>
								<div>
									<SubmitButton variant="default" pending={save.isPending} pendingLabel={t.options.saving}>
										{t.options.save}
									</SubmitButton>
								</div>
							</div>
						</Panel>
					</form>
					<LinkCard token={sheet?.public_token} />
				</div>
				<Panel title={t.preview.title}>
					<div className="p-3">
						<Preview version={version} />
					</div>
				</Panel>
			</div>
		</div>
	);
}
