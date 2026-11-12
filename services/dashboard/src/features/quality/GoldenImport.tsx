import { Download } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
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
import { downloadText } from "@/features/insights/download";
import { useErrorText } from "@/features/insights/ui";
import type { Corpus } from "@/lib/api/queries";
import { en, fill } from "@/lib/strings/en";
import { qualityStrings } from "@/lib/strings/insights";
import { useImportGolden } from "./api";
import { GOLDEN_TEMPLATE, type GoldenPreview, previewGoldenCsv } from "./golden-csv";

const t = qualityStrings.importDialog;

function previewError(p: GoldenPreview): string | null {
	if (p.ok) return null;
	if (p.error === "missing_columns") return t.missingColumns;
	return fill(t.badChunks, { row: p.row, id: p.id });
}

/**
 * Import a CSV into the golden set (`POST /golden/import`): the file is checked in the browser
 * first (columns, rows the engine will skip, passage ids), with a template to download.
 */
export function GoldenImport({
	open,
	onOpenChange,
	kb,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	kb: Corpus;
}) {
	const id = useId();
	const [file, setFile] = useState<File | null>(null);
	const [preview, setPreview] = useState<GoldenPreview | null>(null);
	const [error, setError] = useState<string | null>(null);
	const run = useImportGolden(kb.id);
	const errorText = useErrorText();

	useEffect(() => {
		if (open) {
			setFile(null);
			setPreview(null);
			setError(null);
		}
	}, [open]);

	const onFile = async (f: File | null) => {
		setFile(f);
		setPreview(null);
		setError(null);
		if (!f) return;
		if (!f.name.toLowerCase().endsWith(".csv")) {
			setError(t.notCsv);
			return;
		}
		const p = previewGoldenCsv(await f.text());
		setPreview(p);
		setError(previewError(p));
	};

	const submit = (e: React.FormEvent) => {
		e.preventDefault();
		if (run.isPending) return;
		if (!file || !preview?.ok || preview.ready === 0) {
			setError(file && preview && !preview.ok ? previewError(preview) : t.notCsv);
			document.getElementById(id)?.focus();
			return;
		}
		setError(null);
		run.mutate(file, {
			onSuccess: (r) => {
				const n = r.imported_count;
				toast.success(n === 1 ? t.imported.one : fill(t.imported.many, { n }));
				onOpenChange(false);
			},
			onError: (e) => setError(errorText(e)),
		});
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-[560px]">
				<DialogHeader>
					<DialogTitle>{t.title}</DialogTitle>
					<DialogDescription>{t.lead}</DialogDescription>
				</DialogHeader>
				<div>
					<Button
						type="button"
						variant="link"
						onClick={() => downloadText(GOLDEN_TEMPLATE, "golden-template.csv", "text/csv;charset=utf-8")}
					>
						<Download aria-hidden />
						{t.template}
					</Button>
				</div>
				<form noValidate onSubmit={submit} className="grid gap-4">
					<Field id={id} label={t.file} error={error}>
						<Input
							id={id}
							type="file"
							accept=".csv,text/csv"
							aria-invalid={!!error || undefined}
							aria-describedby={describedBy(id, { error: !!error })}
							onChange={(e) => void onFile(e.target.files?.[0] ?? null)}
						/>
					</Field>
					{preview?.ok ? (
						<FormAlert tone="info">
							<span data-testid="import-preview">
								{fill(t.preview, { n: preview.ready, skipped: preview.skipped })}
							</span>
						</FormAlert>
					) : null}
					<DialogFooter>
						<Button type="button" onClick={() => onOpenChange(false)}>
							{en.common.cancel}
						</Button>
						<SubmitButton variant="default" pending={run.isPending}>
							{t.submit}
						</SubmitButton>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
