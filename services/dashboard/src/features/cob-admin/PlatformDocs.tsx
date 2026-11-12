import type { UseQueryResult } from "@tanstack/react-query";
import { FileUp, Trash2 } from "lucide-react";
import { useId, useRef, useState } from "react";
import { toast } from "sonner";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { TypedConfirmDialog } from "@/components/patterns/TypedConfirmDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FormFoot, Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatRelative } from "@/lib/format/date";
import { cobAdminStrings as t } from "@/lib/strings/cob-admin";
import { fill } from "@/lib/strings/en";
import {
	AUDIENCES,
	type CobAudience,
	type CobDocument,
	useAddPlatformNote,
	useDeletePlatformDoc,
	useSetDocAudience,
	useUploadPlatformDoc,
} from "./api";
import { audienceLabel, docName } from "./docs-ui";

type Docs = { items: CobDocument[]; corpus_id?: string | null };

const fail = (title: string, e: unknown) => toast.error(title, { description: messageFor(toApiError(e)).title });

function AudienceSelect({
	value,
	onChange,
	label,
	disabled,
}: {
	value: CobAudience;
	onChange: (a: CobAudience) => void;
	label: string;
	disabled?: boolean;
}) {
	return (
		<NativeSelect
			aria-label={label}
			className="w-56 max-w-full"
			value={value}
			disabled={disabled}
			onChange={(e) => onChange(e.target.value as CobAudience)}
		>
			{AUDIENCES.map((a) => (
				<option key={a} value={a}>
					{audienceLabel(a)}
				</option>
			))}
		</NativeSelect>
	);
}

function AddPanel({ corpusId }: { corpusId: string | null | undefined }) {
	const [audience, setAudience] = useState<CobAudience>("customer");
	const [title, setTitle] = useState("");
	const [text, setText] = useState("");
	const [error, setError] = useState<string | null>(null);
	const file = useRef<HTMLInputElement>(null);
	const titleId = useId();
	const textId = useId();
	const upload = useUploadPlatformDoc(corpusId);
	const note = useAddPlatformNote(corpusId);
	const ready = !!corpusId;

	const send = (f: File) =>
		upload.mutate(
			{ file: f, audience },
			{
				onSuccess: () => toast.success(fill(t.platform.uploaded, { name: f.name })),
				onError: (e) => fail(t.platform.addFailed, e),
			},
		);
	const saveNote = () => {
		if (!title.trim()) return setError(t.platform.noteTitleRequired);
		if (!text.trim()) return setError(t.platform.noteTextRequired);
		setError(null);
		note.mutate(
			{ title, text, audience },
			{
				onSuccess: () => {
					toast.success(t.platform.noteAdded);
					setTitle("");
					setText("");
				},
				onError: (e) => fail(t.platform.addFailed, e),
			},
		);
	};

	return (
		<Panel title={t.platform.addNote} sub={t.platform.note}>
			<div className="grid gap-4 p-4">
				<div className="flex flex-wrap items-center gap-3">
					<AudienceSelect value={audience} onChange={setAudience} label={t.audience.label} />
					<input
						ref={file}
						type="file"
						className="sr-only"
						aria-label={t.platform.upload}
						tabIndex={-1}
						onChange={(e) => {
							const f = e.target.files?.[0];
							if (f) send(f);
							e.target.value = "";
						}}
					/>
					<Button variant="outline" disabled={!ready || upload.isPending} onClick={() => file.current?.click()}>
						<FileUp aria-hidden />
						{t.platform.upload}
					</Button>
					<span className="text-[12.5px] text-muted-foreground">{t.platform.uploadHelp}</span>
				</div>
				<div className="grid gap-1.5">
					<label htmlFor={titleId} className="text-[13px] font-semibold">
						{t.platform.noteTitle}
					</label>
					<Input id={titleId} value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
				</div>
				<div className="grid gap-1.5">
					<label htmlFor={textId} className="text-[13px] font-semibold">
						{t.platform.noteText}
					</label>
					<Textarea id={textId} rows={5} value={text} onChange={(e) => setText(e.target.value)} />
				</div>
				{error ? (
					<p role="alert" className="m-0 text-[13px] text-destructive">
						{error}
					</p>
				) : null}
			</div>
			<FormFoot>
				<Button disabled={!ready || note.isPending} onClick={saveNote}>
					{t.platform.noteSave}
				</Button>
			</FormFoot>
		</Panel>
	);
}

/** The operator's own pages: add, change who can see each, delete with the page name typed. */
export function PlatformDocs({ query }: { query: UseQueryResult<Docs> }) {
	const corpusId = query.data?.corpus_id;
	const rows = (query.data?.items ?? []).filter((d) => d.source === "platform");
	const setAudience = useSetDocAudience();
	const remove = useDeletePlatformDoc(corpusId);
	const [deleting, setDeleting] = useState<CobDocument | null>(null);

	const columns: Column<CobDocument>[] = [
		{ id: "name", header: t.platform.name, rowHeader: true, cell: (d) => docName(d), sortValue: (d) => d.name },
		{
			id: "audience",
			header: t.platform.audience,
			cell: (d) => (
				<AudienceSelect
					value={d.audience}
					label={fill(t.platform.changeAudience, { name: docName(d) })}
					disabled={setAudience.isPending}
					onChange={(audience) =>
						setAudience.mutate(
							{ id: d.id, audience },
							{
								onSuccess: () => toast.success(fill(t.platform.audienceChanged, { who: audienceLabel(audience) })),
								onError: (e) => fail(t.platform.audienceFailed, e),
							},
						)
					}
				/>
			),
		},
		{
			id: "added",
			header: t.platform.added,
			cell: (d) => formatRelative(d.ingested_at),
			sortValue: (d) => d.ingested_at,
		},
		{
			id: "actions",
			header: "",
			align: "right",
			cell: (d) => (
				<Button
					variant="ghost"
					size="sm"
					aria-label={`${t.platform.delete} ${docName(d)}`}
					onClick={() => setDeleting(d)}
				>
					<Trash2 aria-hidden />
					{t.platform.delete}
				</Button>
			),
		},
	];

	return (
		<div className="grid gap-4">
			<AddPanel corpusId={corpusId} />
			<Panel title={t.tabs.platform}>
				{query.isPending ? (
					<TableSkeleton rows={4} />
				) : query.isError ? (
					<div className="p-3">
						<ErrorState error={query.error} title={t.platform.loadError} onRetry={() => void query.refetch()} />
					</div>
				) : rows.length === 0 ? (
					<EmptyState title={t.platform.empty} />
				) : (
					<div className="overflow-x-auto">
						<DataTable caption={t.platform.caption} columns={columns} rows={rows} rowKey={(d) => d.id} />
					</div>
				)}
			</Panel>
			<TypedConfirmDialog
				open={deleting !== null}
				onOpenChange={(open) => !open && setDeleting(null)}
				title={fill(t.platform.deleteTitle, { name: deleting ? docName(deleting) : "" })}
				body={t.platform.deleteBody}
				word={deleting ? docName(deleting) : ""}
				confirmLabel={t.platform.deleteConfirm}
				pending={remove.isPending}
				onConfirm={() => {
					if (!deleting) return;
					const name = docName(deleting);
					remove.mutate(deleting.id, {
						onSuccess: () => {
							toast.success(fill(t.platform.deleted, { name }));
							setDeleting(null);
						},
						onError: (e) => {
							fail(t.platform.deleteFailed, e);
							setDeleting(null);
						},
					});
				}}
			/>
		</div>
	);
}
