import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { type ReactNode, useEffect, useId, useMemo } from "react";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { StatusBadge } from "@/components/patterns/StatusBadge";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useErrorText, useKbNames } from "@/features/insights/ui";
import { Panel } from "@/features/kbs/ui";
import { corporaSwitcherQuery } from "@/lib/api/queries";
import { formatDateTime } from "@/lib/format/date";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { en, fill } from "@/lib/strings/en";
import { RUNS_PAGE_SIZE } from "./api";

/** Shared parts of the evaluation and optimisation tabs (admin only). */

export interface RunRow {
	id: string;
	status: string;
	corpus_id: string | null;
	created_at: string;
	completed_at: string | null;
}

export function RunsList<T extends RunRow>({
	title,
	sub,
	rows,
	total,
	pending,
	error,
	onRetry,
	page,
	onPage,
	onOpen,
	aside,
	empty,
	errorTitle,
	labels,
	amount,
}: {
	title: string;
	sub: string;
	rows: T[] | undefined;
	total: number;
	pending: boolean;
	error: unknown;
	onRetry: () => void;
	page: number;
	onPage: (p: number) => void;
	onOpen: (id: string) => void;
	aside: ReactNode;
	empty: string;
	errorTitle: string;
	labels: {
		started: string;
		kb: string;
		amount: string;
		status: string;
		finished: string;
		anyKb: string;
		open: string;
	};
	amount: (r: T) => ReactNode;
}) {
	const kbName = useKbNames();
	const columns: Column<T>[] = [
		{
			id: "started",
			header: labels.started,
			rowHeader: true,
			cell: (r) => (
				<button
					type="button"
					className="text-left font-semibold underline-offset-4 hover:underline"
					aria-label={fill(labels.open, { date: formatDateTime(r.created_at) })}
					onClick={() => onOpen(r.id)}
				>
					{formatDateTime(r.created_at)}
				</button>
			),
		},
		{ id: "kb", header: labels.kb, stackLabel: true, cell: (r) => (r.corpus_id ? kbName(r.corpus_id) : labels.anyKb) },
		{ id: "amount", header: labels.amount, align: "right", stackLabel: true, cell: amount },
		{ id: "status", header: labels.status, stackLabel: true, cell: (r) => <StatusBadge status={r.status} /> },
		{
			id: "finished",
			header: labels.finished,
			stackLabel: true,
			cell: (r) => (r.completed_at ? formatDateTime(r.completed_at) : "—"),
		},
	];
	return (
		<Panel title={title} sub={sub} aside={aside}>
			{error ? (
				<div className="p-4">
					<ErrorState error={error} title={errorTitle} onRetry={onRetry} />
				</div>
			) : pending || !rows ? (
				<div className="p-4">
					<TableSkeleton rows={3} />
				</div>
			) : (
				<>
					<div className="px-2">
						<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} caption={title} empty={empty} />
					</div>
					{total > RUNS_PAGE_SIZE ? (
						<Pager page={page} pageSize={RUNS_PAGE_SIZE} total={total} onPage={onPage} />
					) : null}
				</>
			)}
		</Panel>
	);
}

/** "Run evaluation" / "Run optimisation": knowledge base (optional) + one optional whole number. */
export function RunDialog({
	open,
	onOpenChange,
	defaultKb,
	strings,
	pending,
	onSubmit,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	defaultKb: string;
	strings: {
		title: string;
		lead: string;
		kb: string;
		anyKb: string;
		number: string;
		numberHint: string;
		bad: string;
		submit: string;
	};
	pending: boolean;
	onSubmit: (v: { corpus_id: string | null; n: number | null }) => Promise<void>;
}) {
	const ids = { kb: useId(), n: useId() };
	const kbs = useQuery(corporaSwitcherQuery);
	const errorText = useErrorText();
	const schema = useMemo(
		() =>
			z.object({
				kb: z.string(),
				n: z.string().refine(
					(v) => {
						const trimmed = v.trim();
						if (trimmed === "") return true;
						const num = Number(trimmed);
						return Number.isInteger(num) && num >= 1;
					},
					{ message: strings.bad },
				),
			}),
		[strings.bad],
	);
	const form = useAppForm<{ kb: string; n: string }>({
		resolver: zodResolver(schema),
		defaultValues: { kb: defaultKb, n: "" },
	});
	useEffect(() => {
		if (open) form.reset({ kb: defaultKb, n: "" });
	}, [open, defaultKb, form]);
	const submit = useAppSubmit(form, async (v) => {
		const trimmed = v.n.trim();
		await onSubmit({ corpus_id: v.kb || null, n: trimmed === "" ? null : Number(trimmed) });
		onOpenChange(false);
	});
	const error = form.formState.errors.n?.message;
	const items = [...(kbs.data?.items ?? [])].sort((a, b) => a.name.localeCompare(b.name, "en-IN"));

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-[520px]">
				<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
					<DialogHeader>
						<DialogTitle>{strings.title}</DialogTitle>
						<DialogDescription>{strings.lead}</DialogDescription>
					</DialogHeader>
					{submit.error ? <FormAlert>{errorText(submit.error)}</FormAlert> : null}
					<Field id={ids.kb} label={strings.kb}>
						<NativeSelect id={ids.kb} {...form.register("kb")}>
							<option value="">{strings.anyKb}</option>
							{items.map((k) => (
								<option key={k.id} value={k.id}>
									{k.tenant_name ? `${k.name} · ${k.tenant_name}` : k.name}
								</option>
							))}
						</NativeSelect>
					</Field>
					<Field id={ids.n} label={strings.number} hint={strings.numberHint} error={error}>
						<Input
							id={ids.n}
							inputMode="numeric"
							className="w-[140px]"
							aria-invalid={!!error || undefined}
							aria-describedby={describedBy(ids.n, { hint: true, error: !!error })}
							{...form.register("n")}
						/>
					</Field>
					<DialogFooter>
						<Button type="button" onClick={() => onOpenChange(false)}>
							{en.common.cancel}
						</Button>
						<SubmitButton variant="default" pending={pending || submit.isSubmitting} locked={submit.locked}>
							{strings.submit}
						</SubmitButton>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}

export function BackButton({ label, onClick }: { label: string; onClick: () => void }) {
	return (
		<div>
			<Button type="button" variant="ghost" size="sm" onClick={onClick}>
				<ArrowLeft aria-hidden />
				{label}
			</Button>
		</div>
	);
}
