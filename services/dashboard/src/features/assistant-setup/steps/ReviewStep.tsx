import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type ReactNode, useState } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import { assistantStateQuery } from "@/features/assistant/api";
import type { FullConfig } from "@/features/assistant/types";
import { KbErrorAlert } from "@/features/kbs/KbErrorAlert";
import { ApiError } from "@/lib/api/errors";
import { fill } from "@/lib/strings/en";
import { setupStrings as t } from "@/lib/strings/setup";
import { type SetupSession, setupQuestionsQuery, useRunSetupTests } from "../api";
import { DAYS } from "../HoursEditor";
import { ProvenanceBadge } from "../ProvenanceBadge";

const TONES: Record<string, string> = {
	friendly: "Friendly",
	professional: "Professional",
	warm: "Warm and caring",
	matter_of_fact: "Short and to the point",
	playful: "Playful",
};
const LANGUAGES: Record<string, string> = {
	en: "English",
	hi: "Hindi",
	"hi-Latn": "Hinglish",
	mr: "Marathi",
	ta: "Tamil",
	te: "Telugu",
	bn: "Bengali",
	gu: "Gujarati",
	kn: "Kannada",
	ml: "Malayalam",
	pa: "Punjabi",
};
const dayName = (d: string): string => (t.proposal.days as Record<string, string>)[d] ?? d;

function hoursText(card: FullConfig["identity"]["card"]): string {
	return [...card.hours]
		.sort((a, b) => DAYS.indexOf(a.day) - DAYS.indexOf(b.day))
		.map((h) => `${dayName(h.day)} ${h.open}–${h.close}`)
		.join(", ");
}

interface Row {
	key: string;
	label: string;
	value: ReactNode;
	source: string | undefined;
	note?: string;
}

/** The warnings the owner should see next to the draft, worked out from what was generated. */
export function setupWarnings(config: FullConfig, answers: Record<string, unknown>): string[] {
	const out: string[] = [];
	const followup = answers.lead_followup;
	if (typeof followup === "string" && followup !== "no_leads" && !config.leads.enabled) {
		out.push("leads_need_encryption_key");
	}
	if (answers.when_unsure === "form" && !config.leads.enabled) out.push("lead_form_needs_leads");
	const need: Record<string, "phone" | "whatsapp" | "email"> = { whatsapp: "whatsapp", call: "phone", email: "email" };
	const kind = typeof answers.when_unsure === "string" ? need[answers.when_unsure] : undefined;
	if (kind && !config.identity.card[kind]) out.push(`contact_missing:${kind}`);
	return out;
}

function rowsOf(cfg: FullConfig, session: SetupSession): Row[] {
	const p = session.provenance;
	const id = cfg.identity;
	const setupRules = (prefix: string) => cfg.rules.filter((r) => r.id.startsWith(prefix));
	const never = setupRules("setup-never-");
	const exact = setupRules("setup-exact-");
	const unsure = t.review.unsure[cfg.conversation.cta.buying_intent_cta] ?? cfg.conversation.cta.buying_intent_cta;
	const list = (items: string[]): ReactNode =>
		items.length ? (
			<ul className="m-0 grid list-none gap-0.5 p-0">
				{items.map((i) => (
					<li key={i}>{i}</li>
				))}
			</ul>
		) : (
			t.review.noValue
		);
	return [
		{
			key: "business",
			label: t.review.rows.business,
			value: id.business_name || t.review.noValue,
			source: p["identity.business_name"],
		},
		{
			key: "assistant",
			label: t.review.rows.assistant,
			value: id.assistant_name,
			source: p["identity.assistant_name"],
		},
		{
			key: "audience",
			label: t.review.rows.audience,
			value: id.audience || t.review.noValue,
			source: p["identity.audience"],
		},
		{
			key: "tone",
			label: t.review.rows.tone,
			value: TONES[cfg.voice.tone_preset] ?? cfg.voice.tone_preset,
			source: p["voice.tone_preset"],
		},
		{
			key: "languages",
			label: t.review.rows.languages,
			value: cfg.language.supported.map((l) => LANGUAGES[l] ?? l).join(", "),
			source: p["language.supported"],
		},
		{
			key: "topics",
			label: t.review.rows.topics,
			value: list(cfg.scope.topics.map((tp) => tp.name)),
			source: p["scope.topics"],
			note: t.review.startersNote,
		},
		{
			key: "never",
			label: t.review.rows.never,
			value: list(never.map((r) => r.instruction)),
			source: never[0] ? p[`rules.${never[0].id}`] : undefined,
		},
		{
			key: "exact",
			label: t.review.rows.exact,
			value: list(exact.map((r) => r.instruction)),
			source: exact[0] ? p[`rules.${exact[0].id}`] : undefined,
		},
		{ key: "unsure", label: t.review.rows.unsure, value: unsure, source: p["conversation.cta.buying_intent_cta"] },
		{
			key: "leads",
			label: t.review.rows.leads,
			value: `${cfg.leads.enabled ? t.review.on : t.review.off}: ${cfg.leads.confirmation_message.en ?? ""}`.trim(),
			source: p["leads.confirmation_message"] ?? p["leads.enabled"],
		},
		{
			key: "phone",
			label: t.review.rows.phone,
			value: id.card.phone || t.review.noValue,
			source: p["identity.card.phone"],
		},
		{
			key: "whatsapp",
			label: t.review.rows.whatsapp,
			value: id.card.whatsapp || t.review.noValue,
			source: p["identity.card.whatsapp"],
		},
		{
			key: "email",
			label: t.review.rows.email,
			value: id.card.email || t.review.noValue,
			source: p["identity.card.email"],
		},
		{
			key: "hours",
			label: t.review.rows.hours,
			value: id.card.hours.length ? hoursText(id.card) : t.review.noValue,
			source: p["identity.card.hours"],
		},
		{
			key: "greeting",
			label: t.review.rows.greeting,
			value: cfg.voice.greeting.en ?? t.review.noValue,
			source: p["voice.greeting"],
		},
	];
}

/**
 * Step 4: what was generated, each item with the answer, page or template line that created it.
 * It reads the saved draft (what Publish will publish), so a reload shows the same thing.
 */
export function ReviewStep({
	kb,
	session,
	wordingUsed,
	onChangeAnswers,
}: {
	kb: string;
	session: SetupSession;
	wordingUsed: "ai" | "template" | null;
	onChangeAnswers: () => void;
}) {
	const state = useQuery(assistantStateQuery(kb));
	const questions = useQuery(setupQuestionsQuery(kb, session.id));
	const run = useRunSetupTests(kb, session.id);
	const [error, setError] = useState<unknown>(null);
	if (state.isPending) return <CardSkeleton lines={8} />;
	if (state.isError) return <ErrorState error={state.error} onRetry={() => void state.refetch()} />;
	const draft = state.data.draft;
	if (!draft) {
		return <ErrorState error={new ApiError({ status: 404, code: "assistant_draft_missing", detail: "" })} />;
	}
	const titles = Object.fromEntries((questions.data?.items ?? []).map((i) => [i.id, i.title]));
	const rows = rowsOf(draft.config, session);
	const warnings = setupWarnings(draft.config, session.answers);
	return (
		<div className="grid gap-4 rounded-lg border border-border bg-card p-5">
			<div className="grid gap-1">
				<h2 className="m-0 text-lg font-semibold">{t.review.heading}</h2>
				<p className="m-0 text-[13px] text-muted-foreground">{t.review.lead}</p>
			</div>
			{wordingUsed === "template" ? (
				<p role="status" className="m-0 rounded-md bg-warning-soft p-3 text-[13px] text-warning">
					{t.interview.fromTemplate}
				</p>
			) : null}
			{warnings.map((w) => (
				<p key={w} role="status" className="m-0 rounded-md bg-warning-soft p-3 text-[13px] text-warning">
					{w === "leads_need_encryption_key"
						? t.review.warnings.leads_need_encryption_key
						: w === "lead_form_needs_leads"
							? t.review.warnings.lead_form_needs_leads
							: fill(t.review.warnings.contact_missing, {
									kind: t.review.contactKinds[w.split(":")[1] ?? ""] ?? "contact",
								})}{" "}
					<Link to="/kbs/$kbId" params={{ kbId: kb }} search={{ tab: "assistant" }} className="font-semibold underline">
						{t.review.edit}
					</Link>
				</p>
			))}
			{error ? <KbErrorAlert error={error} /> : null}
			<section
				aria-label={t.review.heading}
				// biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be keyboard-reachable (WCAG 2.1.1)
				tabIndex={0}
				className="overflow-x-auto rounded-md border border-border focus-visible:outline-2 focus-visible:outline-ring"
			>
				<table className="w-full text-left text-[13.5px]">
					<caption className="sr-only">{t.review.heading}</caption>
					<thead>
						<tr className="border-b border-border bg-muted text-[12.5px] text-muted-foreground">
							<th scope="col" className="px-3 py-2 font-semibold">
								{t.review.colItem}
							</th>
							<th scope="col" className="px-3 py-2 font-semibold">
								{t.review.colValue}
							</th>
							<th scope="col" className="px-3 py-2 font-semibold">
								{t.review.colSource}
							</th>
						</tr>
					</thead>
					<tbody>
						{rows.map((r) => (
							<tr key={r.key} className="border-b border-border align-top last:border-0">
								<th scope="row" className="px-3 py-2 font-semibold">
									{r.label}
								</th>
								<td className="px-3 py-2 break-words">
									{r.value}
									{r.note ? <small className="mt-1 block text-[12px] text-muted-foreground">{r.note}</small> : null}
								</td>
								<td className="px-3 py-2">
									<ProvenanceBadge source={r.source} titles={titles} />
								</td>
							</tr>
						))}
					</tbody>
				</table>
			</section>
			<div className="flex flex-wrap items-center justify-between gap-2">
				<div className="flex flex-wrap gap-2">
					<Button variant="ghost" onClick={onChangeAnswers}>
						{t.review.changeAnswers}
					</Button>
					<Button variant="outline" asChild>
						<Link to="/kbs/$kbId" params={{ kbId: kb }} search={{ tab: "assistant" }}>
							{t.review.edit}
						</Link>
					</Button>
				</div>
				<Button
					variant="default"
					disabled={run.isPending}
					onClick={() => {
						setError(null);
						run.mutateAsync().catch(setError);
					}}
				>
					{run.isPending ? t.review.starting : t.review.runChecks}
				</Button>
			</div>
		</div>
	);
}
