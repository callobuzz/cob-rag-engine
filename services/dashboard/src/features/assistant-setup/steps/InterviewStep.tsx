import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { Controller, type FieldErrors, type Resolver } from "react-hook-form";
import { Field } from "@/components/auth/Field";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { assistantStateQuery } from "@/features/assistant/api";
import { KbErrorAlert } from "@/features/kbs/KbErrorAlert";
import { toApiError } from "@/lib/api/errors";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { fill } from "@/lib/strings/en";
import { setupStrings as t } from "@/lib/strings/setup";
import { type SetupQuestion, type SetupSession, setupQuestionsQuery, useAnswerSetup, useGenerateSetup } from "../api";
import { ChoiceCards } from "../ChoiceCards";
import { HoursEditor, type HoursRow, hoursProblem, isHoursRow } from "../HoursEditor";

const OTHER = "__other";

/** What the form holds for one question; `toServer` turns it back into the engine's answer. */
type Choice = { picks: string[]; other: string };
type Value = Choice | string | HoursRow[];
type Form = { answers: Record<string, Value> };

const isOtherAnswer = (v: unknown): v is { other: string } =>
	!!v && typeof v === "object" && !Array.isArray(v) && typeof (v as { other?: unknown }).other === "string";

function fromServer(q: SetupQuestion, raw: unknown): Value {
	switch (q.kind) {
		case "single":
		case "multi": {
			const items = Array.isArray(raw) ? raw : raw == null || raw === "" ? [] : [raw];
			const picks: string[] = [];
			let other = "";
			for (const item of items) {
				if (isOtherAnswer(item)) {
					picks.push(OTHER);
					other = item.other;
				} else if (typeof item === "string") picks.push(item);
			}
			return { picks, other };
		}
		case "list":
			return Array.isArray(raw) ? raw.map(String).join("\n") : "";
		case "hours":
			return Array.isArray(raw) ? raw.filter(isHoursRow) : [];
		default:
			return typeof raw === "string" ? raw : "";
	}
}

function toServer(q: SetupQuestion, v: Value | undefined): unknown {
	if (v === undefined) return undefined;
	switch (q.kind) {
		case "single": {
			const c = v as Choice;
			const pick = c.picks[0];
			if (pick === OTHER) return c.other.trim() ? { other: c.other.trim() } : undefined;
			return pick;
		}
		case "multi": {
			const c = v as Choice;
			const plain = c.picks.filter((p) => p !== OTHER);
			return c.picks.includes(OTHER) && c.other.trim() ? [...plain, { other: c.other.trim() }] : plain;
		}
		case "list":
			return String(v)
				.split("\n")
				.map((l) => l.trim())
				.filter(Boolean);
		case "hours":
			return v as HoursRow[];
		default:
			return String(v).trim();
	}
}

const isEmpty = (a: unknown): boolean => a === undefined || a === "" || (Array.isArray(a) && a.length === 0);

/** The message for an answer that cannot be sent, or null. */
function problem(q: SetupQuestion, v: Value | undefined): string | null {
	if (q.kind === "hours") {
		const rows = (v ?? []) as HoursRow[];
		return hoursProblem(rows);
	}
	if ((q.kind === "single" || q.kind === "multi") && v && (v as Choice).picks.includes(OTHER)) {
		if (!(v as Choice).other.trim()) return t.interview.reasons.invalid_other ?? t.interview.fallbackReason;
	}
	if (q.required && isEmpty(toServer(q, v))) return t.interview.answerRequired;
	return null;
}

const reasonText = (reason: string): string => t.interview.reasons[reason] ?? t.interview.fallbackReason;

export function InterviewStep({
	kb,
	session,
	onGenerated,
}: {
	kb: string;
	session: SetupSession;
	onGenerated: (r: { wordingUsed: "ai" | "template"; warnings: string[] }) => void;
}) {
	const q = useQuery(setupQuestionsQuery(kb, session.id));
	if (q.isPending) return <CardSkeleton lines={5} />;
	if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
	const profileHasHours = Array.isArray(q.data.profile.hours) && q.data.profile.hours.length > 0;
	// The confirmed profile already holds the opening hours; asking again would only repeat it.
	const items = q.data.items.filter((i) => !(i.id === "hours" && profileHasHours));
	return <Interview kb={kb} session={session} items={items} onGenerated={onGenerated} />;
}

function Interview({
	kb,
	session,
	items,
	onGenerated,
}: {
	kb: string;
	session: SetupSession;
	items: SetupQuestion[];
	onGenerated: (r: { wordingUsed: "ai" | "template"; warnings: string[] }) => void;
}) {
	const qc = useQueryClient();
	const answer = useAnswerSetup(kb, session.id);
	const generate = useGenerateSetup(kb, session.id);
	const base = useId();
	const questions = items.filter((i) => i.skipped_reason == null);
	const skipped = items.filter((i) => i.skipped_reason != null);
	const [index, setIndex] = useState(0);
	const current = questions[index];
	const currentRef = useRef(current);
	currentRef.current = current;
	const total = questions.length;
	const last = index === total - 1;

	const resolver: Resolver<Form> = (values) => {
		const cq = currentRef.current;
		const message = cq ? problem(cq, values.answers[cq.id]) : null;
		if (!cq || !message) return { values, errors: {} };
		return {
			values: {},
			errors: { answers: { [cq.id]: { type: "validate", message } } } as unknown as FieldErrors<Form>,
		};
	};
	const form = useAppForm<Form>({
		resolver,
		defaultValues: { answers: Object.fromEntries(questions.map((qu) => [qu.id, fromServer(qu, qu.prefill)])) },
	});
	const [written, setWritten] = useState<"idle" | "writing">("idle");

	const everything = (values: Form): Record<string, unknown> => {
		const out: Record<string, unknown> = {};
		for (const qu of questions) {
			const v = toServer(qu, values.answers[qu.id]);
			if (!isEmpty(v)) out[qu.id] = v;
		}
		// Answers to questions this screen does not ask (the hours the profile already holds, skipped
		// ones) stay as the session has them.
		for (const [k, v] of Object.entries(session.answers))
			if (!(k in out) && !questions.some((qu) => qu.id === k)) out[k] = v;
		return out;
	};

	const submit = useAppSubmit(
		form,
		async (values) => {
			const answers = everything(values);
			if (!last) {
				await answer.mutateAsync({ answers, partial: true });
				setIndex((i) => i + 1);
				return;
			}
			await answer.mutateAsync({ answers, partial: false });
			setWritten("writing");
			try {
				const fresh = await qc.fetchQuery({ ...assistantStateQuery(kb), staleTime: 0 });
				const base_revision = fresh.draft?.draft_revision ?? 0;
				let out: Awaited<ReturnType<typeof generate.mutateAsync>>;
				try {
					out = await generate.mutateAsync({ wording: "ai", base_revision });
				} catch (e) {
					if (toApiError(e).code !== "assistant_llm_unavailable") throw e;
					out = await generate.mutateAsync({ wording: "template", base_revision });
				}
				onGenerated({ wordingUsed: out.wording_used, warnings: out.warnings });
			} finally {
				setWritten("idle");
			}
		},
		{
			fieldName: (path) => (path.startsWith("answers.") ? (path as `answers.${string}`) : null),
			onError: (err) => {
				const hit = err.fieldErrors.find((f) => f.path.startsWith("answers."));
				if (!hit) return false;
				const id = hit.path.slice("answers.".length);
				const at = questions.findIndex((qu) => qu.id === id);
				if (at >= 0) {
					setIndex(at);
					form.setError(`answers.${id}`, { type: "server", message: reasonText(hit.message) });
				}
				return at >= 0;
			},
		},
	);

	// A new question starts clean: the previous one's error does not follow the owner.
	// biome-ignore lint/correctness/useExhaustiveDependencies: only the question changing matters
	useEffect(() => {
		form.clearErrors();
	}, [index]);

	if (!current) return null;
	const err = form.formState.errors.answers?.[current.id];
	const message = err && typeof err === "object" && "message" in err ? String(err.message) : null;
	const fid = `${base}-${current.id}`;

	return (
		<form onSubmit={submit.onSubmit} noValidate className="grid gap-4 rounded-lg border border-border bg-card p-5">
			<div className="grid gap-1">
				<p aria-live="polite" className="m-0 text-[12.5px] font-semibold text-muted-foreground">
					{fill(t.interview.progress, { n: index + 1, total })}
				</p>
				<h2 id={`${fid}-title`} className="m-0 text-lg font-semibold">
					{current.title}
				</h2>
				{current.help ? <p className="m-0 text-[13px] text-muted-foreground">{current.help}</p> : null}
				{current.kind === "hours" && Array.isArray(session.proposal?.hours) && session.proposal.hours.length ? (
					<Badge variant="info" className="mt-1">
						{t.interview.hoursFromWebsite}
					</Badge>
				) : null}
			</div>
			{submit.error ? <KbErrorAlert error={submit.error} /> : null}
			<Controller
				key={current.id}
				control={form.control}
				name={`answers.${current.id}`}
				render={({ field }) => (
					<QuestionInput q={current} id={fid} value={field.value as Value} onChange={field.onChange} error={message} />
				)}
			/>
			{message ? (
				<small id={`${fid}-error`} role="alert" className="text-[12.5px] font-medium text-destructive">
					{message}
				</small>
			) : null}
			{written === "writing" ? (
				<p role="status" className="m-0 text-[13px] text-muted-foreground">
					{t.interview.writing}
				</p>
			) : null}
			{skipped.length && index === 0 ? (
				<p className="m-0 text-[12.5px] text-muted-foreground">
					{fill(t.interview.skipped, { titles: skipped.map((s) => s.title).join(", ") })}
				</p>
			) : null}
			<div className="flex justify-between gap-2">
				<Button
					type="button"
					variant="ghost"
					disabled={index === 0 || submit.isSubmitting}
					onClick={() => setIndex((i) => i - 1)}
				>
					{t.interview.back}
				</Button>
				<SubmitButton
					variant="default"
					pending={submit.isSubmitting}
					locked={submit.locked}
					pendingLabel={written === "writing" ? t.interview.writing : t.interview.saving}
				>
					{last ? t.interview.finish : t.interview.next}
				</SubmitButton>
			</div>
		</form>
	);
}

function QuestionInput({
	q,
	id,
	value,
	onChange,
	error,
}: {
	q: SetupQuestion;
	id: string;
	value: Value;
	onChange: (next: Value) => void;
	error: string | null;
}) {
	const described = error ? `${id}-error` : undefined;
	if (q.kind === "single" || q.kind === "multi") {
		const c = (value ?? { picks: [], other: "" }) as Choice;
		const choices = [
			...q.options.map((o) => ({ value: String(o.value), label: String(o.label) })),
			...(q.other ? [{ value: OTHER, label: t.interview.otherOption }] : []),
		];
		return (
			<div className="grid gap-2">
				<ChoiceCards
					legend={q.title}
					type={q.kind === "single" ? "radio" : "checkbox"}
					name={id}
					choices={choices}
					value={c.picks}
					invalid={!!error}
					describedBy={described}
					onChange={(picks) => {
						const capped = q.max_items && picks.length > q.max_items ? picks.slice(0, q.max_items) : picks;
						onChange({ picks: capped, other: c.other });
					}}
				/>
				{c.picks.includes(OTHER) ? (
					<Field id={`${id}-other`} label={t.interview.otherLabel}>
						<Input
							id={`${id}-other`}
							value={c.other}
							maxLength={200}
							onChange={(e) => onChange({ picks: c.picks, other: e.target.value })}
						/>
					</Field>
				) : null}
			</div>
		);
	}
	if (q.kind === "list") {
		return (
			<div className="grid gap-1.5">
				<label htmlFor={id} className="sr-only">
					{q.title}
				</label>
				<Textarea
					id={id}
					rows={6}
					value={String(value ?? "")}
					onChange={(e) => onChange(e.target.value)}
					aria-invalid={error ? true : undefined}
					aria-describedby={[`${id}-hint`, described].filter(Boolean).join(" ")}
				/>
				<small id={`${id}-hint`} className="text-[12.5px] text-muted-foreground">
					{t.interview.listHint}
				</small>
			</div>
		);
	}
	if (q.kind === "hours") {
		return <HoursEditor idPrefix={id} value={(value ?? []) as HoursRow[]} onChange={onChange} />;
	}
	return (
		<div className="grid gap-1.5">
			<label htmlFor={id} className="sr-only">
				{q.title}
			</label>
			<Input
				id={id}
				value={String(value ?? "")}
				maxLength={40}
				onChange={(e) => onChange(e.target.value)}
				aria-invalid={error ? true : undefined}
				aria-describedby={described}
			/>
		</div>
	);
}
