import { Link } from "@tanstack/react-router";
import { CircleAlert, FileJson, RotateCcw } from "lucide-react";
import { useId, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Panel } from "@/features/kbs/ui";
import type { EngineInfo } from "@/lib/api/accounts-types";
import { messageFor } from "@/lib/api/error-messages";
import { type ApiError, toApiError } from "@/lib/api/errors";
import { formatNumber } from "@/lib/format/number";
import { contentStrings as t } from "@/lib/strings/content";
import { fill } from "@/lib/strings/en";
import { recordsImport, useJob } from "./api";
import { JobStages } from "./JobStages";
import { JobStatusBadge } from "./JobsTable";
import { RecordsMapping } from "./RecordsMapping";
import { RecordsPreview } from "./RecordsPreview";
import {
	type BuildResult,
	batches,
	buildRecords,
	guessMapping,
	type Mapping,
	parseCsvRecords,
	parseJsonRecords,
	type Source,
} from "./records";

type Step = "source" | "mapping" | "preview";

interface Batch {
	index: number;
	/** First and last source rows in this batch (1-based). */
	from: number;
	to: number;
	size: number;
	state: "waiting" | "sending" | "sent" | "error" | "stopped";
	jobId: string | null;
	error: ApiError | null;
}

/**
 * Records import (D4): 1 source (paste JSON or a .json / .csv file) → 2 mapping → 3 preview with
 * validation → import in batches of `limits.max_records_per_request`, one job per batch, shown
 * as a group with each batch's created / updated / unchanged counts.
 */
export function RecordsImport({ kb, info }: { kb: string; info: EngineInfo | undefined }) {
	const [step, setStep] = useState<Step>("source");
	const [source, setSource] = useState<(Source & { label: string }) | null>(null);
	const [mapping, setMapping] = useState<Mapping | null>(null);
	const [showMapErrors, setShowMapErrors] = useState(false);
	const [run, setRun] = useState<Batch[] | null>(null);
	const batchSize = Math.min(1000, info?.limits.max_records_per_request ?? 1000);
	const maxChars = info?.limits.max_record_chars ?? 100_000;

	const built: BuildResult | null = useMemo(
		() =>
			source && mapping?.id && mapping.text ? buildRecords(source.rows, mapping, { maxRecordChars: maxChars }) : null,
		[source, mapping, maxChars],
	);

	const reset = () => {
		setStep("source");
		setSource(null);
		setMapping(null);
		setRun(null);
		setShowMapErrors(false);
	};

	const startImport = async () => {
		if (!built || built.records.length === 0) return;
		const groups = batches(
			built.records.map((r, i) => ({ r, row: built.rows[i] ?? i + 1 })),
			batchSize,
		);
		const initial: Batch[] = groups.map((g, i) => ({
			index: i,
			from: g[0]?.row ?? 0,
			to: g[g.length - 1]?.row ?? 0,
			size: g.length,
			state: "waiting",
			jobId: null,
			error: null,
		}));
		setRun(initial);
		const patch = (i: number, p: Partial<Batch>) =>
			setRun((cur) => (cur ? cur.map((b) => (b.index === i ? { ...b, ...p } : b)) : cur));
		for (const [i, g] of groups.entries()) {
			patch(i, { state: "sending" });
			try {
				const res = await recordsImport(
					kb,
					g.map((x) => x.r),
				);
				patch(i, { state: "sent", jobId: res.job_id });
			} catch (e) {
				const err = toApiError(e);
				// Map 422 `records.{i}.field` paths to source rows.
				patch(i, {
					state: "error",
					error: withRows(
						err,
						g.map((x) => x.row),
					),
				});
				setRun((cur) => (cur ? cur.map((b) => (b.index > i ? { ...b, state: "stopped" } : b)) : cur));
				return;
			}
		}
	};

	return (
		<div className="grid gap-4">
			<Steps step={step} />
			{run ? (
				<ImportRun kb={kb} run={run} onStartOver={reset} />
			) : step === "source" ? (
				<SourceStep
					onSource={(s) => {
						setSource(s);
						setMapping(guessMapping(s.columns));
						setStep("mapping");
					}}
				/>
			) : step === "mapping" && source && mapping ? (
				<Panel
					title={t.records.stepMapping}
					sub={fill(t.records.fileLoaded, {
						file: source.label,
						n: formatNumber(source.rows.length),
						c: formatNumber(source.columns.length),
					})}
				>
					<div className="grid gap-4 p-4">
						<RecordsMapping
							columns={source.columns}
							mapping={mapping}
							onChange={setMapping}
							showErrors={showMapErrors}
						/>
						<div className="flex flex-wrap justify-between gap-2">
							<Button variant="outline" onClick={reset}>
								{t.records.back}
							</Button>
							<Button
								variant="default"
								onClick={() => {
									if (!mapping.id || !mapping.text) {
										setShowMapErrors(true);
										return;
									}
									setStep("preview");
								}}
							>
								{t.records.next}
							</Button>
						</div>
					</div>
				</Panel>
			) : step === "preview" && built ? (
				<Panel title={t.records.stepPreview}>
					<div className="grid gap-4 p-4">
						<RecordsPreview built={built} batchSize={batchSize} />
						<div className="flex flex-wrap justify-between gap-2">
							<Button variant="outline" onClick={() => setStep("mapping")}>
								{t.records.back}
							</Button>
							<Button variant="default" disabled={built.records.length === 0} onClick={() => void startImport()}>
								{fill(t.records.import, { n: formatNumber(built.records.length) })}
							</Button>
						</div>
					</div>
				</Panel>
			) : null}
		</div>
	);
}

/** A 422 for batch rows: `records.3.text` → "Row 57: …" using the batch's source rows. */
function withRows(err: ApiError, rows: number[]): ApiError {
	if (!err.fieldErrors.length) return err;
	const mapped = err.fieldErrors.map((f) => {
		const m = /^records\.(\d+)(?:\.(\w+))?/.exec(f.path);
		const row = m ? rows[Number(m[1])] : undefined;
		return row ? { ...f, path: `row ${row}${m?.[2] ? ` (${m[2]})` : ""}` } : f;
	});
	return Object.assign(Object.create(Object.getPrototypeOf(err)), err, { fieldErrors: mapped }) as ApiError;
}

function Steps({ step }: { step: Step }) {
	const all: [Step, string][] = [
		["source", t.records.stepSource],
		["mapping", t.records.stepMapping],
		["preview", t.records.stepPreview],
	];
	const at = all.findIndex(([s]) => s === step);
	return (
		<ol className="m-0 flex list-none flex-wrap gap-2 p-0 text-[12.5px]">
			{all.map(([s, label], i) => (
				<li
					key={s}
					aria-current={s === step ? "step" : undefined}
					className={
						s === step
							? "rounded-full bg-primary-soft px-3 py-1 font-semibold text-primary"
							: i < at
								? "rounded-full bg-secondary px-3 py-1 text-foreground-2"
								: "rounded-full px-3 py-1 text-muted-foreground"
					}
				>
					{label}
				</li>
			))}
		</ol>
	);
}

function SourceStep({ onSource }: { onSource: (s: Source & { label: string }) => void }) {
	const pasteId = useId();
	const hintId = useId();
	const errId = useId();
	const fileRef = useRef<HTMLInputElement>(null);
	const [text, setText] = useState("");
	const [error, setError] = useState<string | null>(null);

	const read = (raw: string, kind: "json" | "csv", label: string) => {
		const r = kind === "json" ? parseJsonRecords(raw) : parseCsvRecords(raw);
		if (!r.ok) {
			setError(r.error);
			return;
		}
		setError(null);
		onSource({ ...r.source, label });
	};

	return (
		<Panel title={t.records.stepSource}>
			<form
				noValidate
				className="grid gap-4 p-4"
				onSubmit={(e) => {
					e.preventDefault();
					if (!text.trim()) {
						setError(t.records.parseEmpty);
						document.getElementById(pasteId)?.focus();
						return;
					}
					read(text, "json", t.records.paste);
				}}
			>
				<p className="m-0 text-[13.5px] text-foreground-2">{t.records.sourceLead}</p>
				<div className="grid gap-1.5">
					<label htmlFor={pasteId} className="text-[13px] font-semibold">
						{t.records.paste}
					</label>
					<Textarea
						id={pasteId}
						rows={6}
						className="font-mono text-[12.5px]"
						value={text}
						aria-describedby={error ? `${hintId} ${errId}` : hintId}
						aria-invalid={error ? true : undefined}
						onChange={(e) => {
							setText(e.target.value);
							setError(null);
						}}
					/>
					<p id={hintId} className="m-0 text-[12.5px] text-muted-foreground">
						{t.records.pasteHint}
					</p>
				</div>
				{error ? (
					<p id={errId} role="alert" className="m-0 flex items-center gap-1.5 text-[13px] text-destructive">
						<CircleAlert className="size-4 shrink-0" aria-hidden />
						{error}
					</p>
				) : null}
				<div className="flex flex-wrap items-center gap-2">
					<Button type="submit" variant="default">
						{t.records.read}
					</Button>
					<span className="text-[13px] text-muted-foreground">{t.records.or}</span>
					<Button type="button" variant="outline" onClick={() => fileRef.current?.click()}>
						<FileJson aria-hidden />
						{t.records.chooseFile}
					</Button>
					<input
						ref={fileRef}
						type="file"
						accept=".json,.csv,application/json,text/csv"
						className="hidden"
						data-testid="records-file"
						tabIndex={-1}
						aria-hidden="true"
						onChange={async (e) => {
							const f = e.target.files?.[0];
							e.target.value = "";
							if (!f) return;
							const name = f.name.toLowerCase();
							const kind = name.endsWith(".json") ? "json" : name.endsWith(".csv") ? "csv" : null;
							if (!kind) {
								setError(t.records.parseErrorFile);
								return;
							}
							read(await f.text(), kind, f.name);
						}}
					/>
				</div>
			</form>
		</Panel>
	);
}

function ImportRun({ kb, run, onStartOver }: { kb: string; run: Batch[]; onStartOver: () => void }) {
	const total = run.reduce((a, b) => a + b.size, 0);
	const finished = run.every((b) => b.state === "sent" || b.state === "error" || b.state === "stopped");
	return (
		<Panel
			title={t.records.progressLabel}
			sub={fill(t.records.total, { n: formatNumber(total) })}
			aside={
				finished ? (
					<Button size="sm" variant="outline" onClick={onStartOver}>
						<RotateCcw aria-hidden />
						{t.records.startOver}
					</Button>
				) : null
			}
		>
			<ol className="m-0 grid list-none p-0" aria-label={t.records.progressLabel}>
				{run.map((b) => (
					<BatchEntry key={b.index} kb={kb} batch={b} count={run.length} />
				))}
			</ol>
		</Panel>
	);
}

function BatchEntry({ kb, batch, count }: { kb: string; batch: Batch; count: number }) {
	const q = useJob(kb, batch.jobId);
	const job = q.data;
	const counts = job?.records ?? null;
	const label = fill(t.records.batch, {
		i: batch.index + 1,
		n: count,
		from: formatNumber(batch.from),
		to: formatNumber(batch.to),
	});
	const msg = batch.error ? messageFor(batch.error) : null;
	return (
		<li data-testid="records-batch" className="grid gap-2 border-b border-border px-4 py-3.5 last:border-b-0">
			<div className="flex flex-wrap items-center justify-between gap-2">
				<b className="text-[13.5px]">{label}</b>
				{job ? <JobStatusBadge status={job.status} /> : null}
			</div>
			{batch.state === "waiting" || batch.state === "sending" ? (
				<p className="m-0 text-[12.5px] text-muted-foreground">
					{batch.state === "waiting" ? t.records.waiting : t.stages.sending}
				</p>
			) : batch.state === "stopped" ? (
				<p className="m-0 text-[12.5px] text-muted-foreground">{t.records.stopped}</p>
			) : batch.state === "error" && msg ? (
				<div role="alert" className="grid gap-1 rounded-md bg-destructive-soft p-2.5 text-[13px] text-destructive">
					<b className="font-semibold">{msg.title}</b>
					{msg.body ? <span>{msg.body}</span> : null}
					{batch.error?.fieldErrors.length ? (
						<ul className="m-0 grid gap-0.5 pl-[18px]">
							{batch.error.fieldErrors.slice(0, 20).map((f) => (
								<li key={`${f.path}-${f.message}`}>
									{f.path}: {f.message}
								</li>
							))}
						</ul>
					) : null}
					{batch.error?.code === "document_limit_reached" ? (
						<Link to="/plan" className="font-semibold text-destructive underline">
							{t.common.seePlan}
						</Link>
					) : null}
				</div>
			) : job ? (
				<>
					{job.status === "completed" ? null : <JobStages status={job.status} name={label} />}
					{counts ? (
						<p className="m-0 text-[13px] text-foreground-2" data-testid="records-counts">
							{fill(t.records.counts, {
								created: formatNumber(counts.created ?? 0),
								updated: formatNumber(counts.updated ?? 0),
								unchanged: formatNumber(counts.unchanged ?? 0),
							})}
							{counts.failed ? fill(t.records.countsFailed, { failed: formatNumber(counts.failed) }) : ""}
						</p>
					) : null}
				</>
			) : null}
		</li>
	);
}
