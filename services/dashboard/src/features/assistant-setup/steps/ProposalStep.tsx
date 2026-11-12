import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useId } from "react";
import { Controller } from "react-hook-form";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { KbErrorAlert } from "@/features/kbs/KbErrorAlert";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { setupStrings as t } from "@/lib/strings/setup";
import { type SetupSession, setupQuestionsQuery, useAnswerSetup } from "../api";
import { HoursEditor, type HoursRow, hoursProblem, isHoursRow } from "../HoursEditor";
import { TEMPLATE_KEYS, typeLabel } from "./StartStep";

const optional = (max: number) => z.string().trim().max(max);
const schema = z.object({
	business_type: z.enum(TEMPLATE_KEYS),
	business_name: optional(120),
	phone: optional(30),
	whatsapp: optional(30),
	email: z
		.string()
		.trim()
		.max(254)
		.refine((v) => v === "" || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v), t.proposal.emailInvalid),
	address: optional(300),
	website: z
		.string()
		.trim()
		.max(2048)
		.refine((v) => v === "" || /^https?:\/\//i.test(v), t.proposal.urlInvalid),
	hours: z
		.array(z.object({ day: z.string(), open: z.string(), close: z.string() }))
		.max(21)
		.superRefine((rows, ctx) => {
			const problem = hoursProblem(rows as HoursRow[]);
			if (problem) ctx.addIssue({ code: "custom", message: problem });
		}),
});
type Form = z.input<typeof schema>;
type Text = "business_name" | "phone" | "whatsapp" | "email" | "address" | "website";

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const nz = (v: string): string | null => (v.trim() === "" ? null : v.trim());

function fieldMessage(e: unknown): string | null {
	if (!e || typeof e !== "object") return null;
	const o = e as { message?: unknown; root?: { message?: unknown } };
	return typeof o.message === "string" ? o.message : typeof o.root?.message === "string" ? o.root.message : null;
}

/**
 * Step 2: confirm what was found (or fill in what you know). The confirmed values are the
 * business profile: the engine saves them on the session and writes them into the business card,
 * ahead of anything the crawl guessed. A value still equal to the crawl's is badged "From your website".
 */
export function ProposalStep({ kb, session }: { kb: string; session: SetupSession }) {
	const q = useQuery(setupQuestionsQuery(kb, session.id));
	if (q.isPending) return <CardSkeleton lines={6} />;
	if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
	return <ProposalForm kb={kb} session={session} profile={q.data.profile} />;
}

function ProposalForm({
	kb,
	session,
	profile,
}: {
	kb: string;
	session: SetupSession;
	profile: { [k: string]: unknown };
}) {
	const answer = useAnswerSetup(kb, session.id);
	const base = useId();
	const proposal = (session.proposal ?? {}) as Record<string, unknown>;
	const website = Boolean(session.website_url);
	const hoursOf = (v: unknown): HoursRow[] => (Array.isArray(v) ? v.filter(isHoursRow) : []);
	const form = useAppForm<Form>({
		resolver: zodResolver(schema),
		defaultValues: {
			business_type: (TEMPLATE_KEYS as readonly string[]).includes(session.business_type ?? "")
				? (session.business_type as (typeof TEMPLATE_KEYS)[number])
				: "generic_support",
			business_name: str(profile.business_name),
			phone: str(profile.phone),
			whatsapp: str(profile.whatsapp),
			email: str(profile.email),
			address: str(profile.address),
			website: str(profile.website),
			hours: hoursOf(profile.hours),
		},
	});
	const values = form.watch();
	const errs = form.formState.errors;
	const submit = useAppSubmit(
		form,
		async (v) => {
			await answer.mutateAsync({
				business_type: v.business_type,
				answers: session.answers,
				partial: true,
				profile: {
					business_name: nz(v.business_name),
					phone: nz(v.phone),
					whatsapp: nz(v.whatsapp),
					email: nz(v.email),
					address: nz(v.address),
					website: nz(v.website),
					hours: v.hours as HoursRow[],
				},
			});
		},
		{ fieldName: (path) => (path.startsWith("profile.") ? (path.slice(8) as Text) : null) },
	);

	const badge = (key: Text | "hours"): ReactNode => {
		const found = proposal[key];
		const mine = values[key];
		const same =
			key === "hours"
				? JSON.stringify(found ?? []) === JSON.stringify(mine ?? []) && Array.isArray(found) && found.length > 0
				: typeof found === "string" && found.trim() !== "" && found.trim() === str(mine).trim();
		return same ? (
			<Badge variant="info" className="shrink-0">
				{t.proposal.fromWebsite}
			</Badge>
		) : null;
	};

	const text = (
		key: Text,
		label: string,
		extra: { type?: string; autoComplete?: string; placeholder?: string } = {},
	) => {
		const id = `${base}-${key}`;
		const message = fieldMessage(errs[key]);
		return (
			<div data-field={key}>
				<Field id={id} label={label} labelExtra={badge(key)} error={message}>
					<Input
						id={id}
						aria-invalid={message ? true : undefined}
						aria-describedby={describedBy(id, { error: !!message })}
						{...extra}
						{...form.register(key)}
					/>
				</Field>
			</div>
		);
	};

	const hoursError = fieldMessage(errs.hours);
	return (
		<form onSubmit={submit.onSubmit} noValidate className="grid gap-4 rounded-lg border border-border bg-card p-5">
			<div className="grid gap-1">
				<h2 className="m-0 text-lg font-semibold">{website ? t.proposal.headingWebsite : t.proposal.headingType}</h2>
				<p className="m-0 text-[13px] text-muted-foreground">
					{website ? t.proposal.leadWebsite : t.proposal.leadType}
				</p>
			</div>
			{website && proposal.read_ok === false ? (
				<p role="status" className="m-0 rounded-md bg-warning-soft p-3 text-[13px] text-warning">
					{t.proposal.notRead}
				</p>
			) : null}
			{submit.error ? <KbErrorAlert error={submit.error} /> : null}
			<div className="grid gap-3 min-[720px]:grid-cols-2">
				<div data-field="business_type">
					<Field id={`${base}-type`} label={t.proposal.businessType}>
						<NativeSelect id={`${base}-type`} {...form.register("business_type")}>
							{TEMPLATE_KEYS.map((k) => (
								<option key={k} value={k}>
									{typeLabel(k)}
								</option>
							))}
						</NativeSelect>
					</Field>
				</div>
				{text("business_name", t.proposal.businessName, { autoComplete: "organization" })}
				{text("phone", t.proposal.phone, { type: "tel", autoComplete: "tel", placeholder: "+91 98220 12345" })}
				{text("whatsapp", t.proposal.whatsapp, { type: "tel", placeholder: "+91 98220 12345" })}
				{text("email", t.proposal.email, { type: "email", autoComplete: "email", placeholder: "care@shreelabs.in" })}
				{text("website", t.proposal.website, { type: "url", placeholder: "https://shreelabs.in" })}
			</div>
			{text("address", t.proposal.address, { autoComplete: "street-address" })}
			<div data-field="hours" className="grid gap-1.5">
				<div className="flex items-baseline justify-between gap-3">
					<span id={`${base}-hours`} className="text-[13px] font-semibold">
						{t.proposal.hours}
					</span>
					{badge("hours")}
				</div>
				<small className="text-[12.5px] text-muted-foreground">{t.proposal.hoursHint}</small>
				<Controller
					control={form.control}
					name="hours"
					render={({ field }) => (
						<HoursEditor
							idPrefix={`${base}-h`}
							value={(field.value ?? []) as HoursRow[]}
							onChange={(next) => field.onChange(next)}
							error={hoursError}
						/>
					)}
				/>
			</div>
			<div className="flex justify-end">
				<SubmitButton
					variant="default"
					pending={submit.isSubmitting}
					locked={submit.locked}
					pendingLabel={t.proposal.saving}
				>
					{t.proposal.looksRight}
				</SubmitButton>
			</div>
		</form>
	);
}
