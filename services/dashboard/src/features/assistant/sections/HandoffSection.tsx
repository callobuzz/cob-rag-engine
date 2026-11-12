import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useFieldArray, useFormContext, useWatch } from "react-hook-form";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { infoQuery } from "@/lib/api/queries";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { fill } from "@/lib/strings/en";
import type { CorpusDetail } from "../../kbs/api";
import { Panel } from "../../kbs/ui";
import { ChipsControl, OptionalNumberControl, SelectControl, SwitchControl, TextControl } from "../controls";
import { LocalizedTextField } from "../LocalizedTextField";
import { SectionShell } from "../SectionShell";
import { type FullConfig, HANDOFF_KINDS, handoffSchema, prettyPhone } from "../schemas";
import type { AssistantState } from "../types";
import { useDraftSection } from "../useDraftSection";
import { NotifyFields } from "./NotifyFields";

type HandoffForm = FullConfig["handoff"];
type Kind = (typeof HANDOFF_KINDS)[number];
type Langs = FullConfig["language"]["supported"];

/** The business card value a channel falls back to, shown as the placeholder of its address field. */
function cardValue(card: FullConfig["identity"]["card"], kind: Kind): string | undefined {
	const pretty = (v: string | null) => (v ? prettyPhone(v) : undefined);
	switch (kind) {
		case "whatsapp":
			return pretty(card.whatsapp);
		case "call":
			return pretty(card.phone);
		case "email":
			return card.email ?? undefined;
		case "contact_page":
			return card.website ?? undefined;
		case "booking":
			return card.booking_url ?? undefined;
		default:
			return undefined;
	}
}

function Channels({ card, languages }: { card: FullConfig["identity"]["card"]; languages: Langs }) {
	const { control, setValue } = useFormContext<HandoffForm>();
	const { fields, append, remove } = useFieldArray({ control, name: "channels" });
	const items = useWatch({ control, name: "channels" }) ?? [];
	const [pick, setPick] = useState<Kind | "">("");
	const missing = HANDOFF_KINDS.filter((k) => !items.some((c) => c.kind === k));
	return (
		<div className="grid gap-3 p-4">
			{fields.length === 0 ? <p className="m-0 text-[13px] text-muted-foreground">{t.handoff.channelsEmpty}</p> : null}
			{fields.map((f, i) => {
				const kind = items[i]?.kind ?? f.kind;
				const name = t.handoff.kinds[kind];
				return (
					<div key={f.id} className="grid gap-3 rounded-lg border border-border p-3">
						<div className="flex flex-wrap items-center gap-3">
							<Switch
								aria-label={fill(t.handoff.channelOn, { kind: name })}
								checked={items[i]?.enabled ?? false}
								onCheckedChange={(v) => setValue(`channels.${i}.enabled`, v, { shouldDirty: true })}
							/>
							<span className="flex-1 text-[14px] font-semibold">{name}</span>
							<Button
								type="button"
								variant="ghost"
								size="sm"
								aria-label={fill(t.handoff.removeChannel, { kind: name })}
								onClick={() => remove(i)}
							>
								{t.form.remove}
							</Button>
						</div>
						<div className="grid gap-3 min-[700px]:grid-cols-2">
							{kind === "callback" ? (
								<small className="self-center text-[12.5px] text-muted-foreground">{t.handoff.callbackNote}</small>
							) : (
								<TextControl
									name={`channels.${i}.value`}
									label={t.handoff.valueLabels[kind]}
									hint={t.handoff.valueNote}
									placeholder={cardValue(card, kind)}
									maxLength={2048}
								/>
							)}
							<SelectControl
								name={`channels.${i}.available`}
								label={fill(t.handoff.channelAvailable, { kind: name })}
								options={(Object.keys(t.handoff.availability) as (keyof typeof t.handoff.availability)[]).map((v) => ({
									value: v,
									label: t.handoff.availability[v],
								}))}
							/>
						</div>
						<LocalizedTextField
							name={`channels.${i}.label`}
							label={fill(t.handoff.channelLabel, { kind: name })}
							max={40}
							rows={1}
							languages={languages}
						/>
					</div>
				);
			})}
			{fields.length < 6 && missing.length ? (
				<div className="flex flex-wrap items-center gap-2">
					<NativeSelect
						className="w-[220px]"
						aria-label={t.handoff.addChannel}
						value={pick}
						onChange={(e) => setPick(e.target.value as Kind | "")}
					>
						<option value="">{t.handoff.addChannelPick}</option>
						{missing.map((k) => (
							<option key={k} value={k}>
								{t.handoff.kinds[k]}
							</option>
						))}
					</NativeSelect>
					<Button
						type="button"
						variant="outline"
						disabled={!pick}
						onClick={() => {
							if (!pick) return;
							append({ kind: pick, enabled: true, label: null, value: null, available: "always" });
							setPick("");
						}}
					>
						{t.handoff.addChannel}
					</Button>
				</div>
			) : null}
		</div>
	);
}

// TV1 human
const MISS_VALUES = [0, 1, 2, 3, 4, 5] as const;

/** The widget's header button. An owner may hide it only where the deployer allows it. */
function HumanButtonSwitch() {
	const info = useQuery(infoQuery);
	const { productName } = useBranding();
	const allowHide = info.data?.features.trust?.allow_hide_human_button === true;
	if (allowHide) {
		return <SwitchControl name="show_human_button" label={t.handoff.showButton} hint={t.handoff.showButtonHint} />;
	}
	return (
		<div className="grid gap-1">
			<div className="flex items-center gap-2.5">
				<Switch id="human-button-locked" checked disabled aria-describedby="human-button-locked-hint" />
				<label htmlFor="human-button-locked" className="text-[13.5px] font-semibold">
					{t.handoff.showButton}
				</label>
			</div>
			<small id="human-button-locked-hint" className="pl-[42px] text-[12.5px] text-muted-foreground">
				{fill(t.handoff.buttonLocked, { product: productName })}
			</small>
		</div>
	);
}
// /TV1 human

/** Talk to a person (design 4.8): how visitors reach your team, and when it is offered. */
export function HandoffSection({ kb, state }: { kb: CorpusDetail; state: AssistantState }) {
	const section = useDraftSection(kb.id, state, "handoff", handoffSchema);
	const languages = section.config.language.supported;
	return (
		<SectionShell section={section}>
			<Panel title={t.handoff.title} sub={t.handoff.sub}>
				<div className="p-4">
					<SwitchControl name="enabled" label={t.handoff.enabled} />
				</div>
			</Panel>
			<Panel title={t.handoff.channelsTitle}>
				<Channels card={section.config.identity.card} languages={languages} />
			</Panel>
			{/* TV1 human */}
			<Panel title={t.handoff.humanTitle} sub={t.handoff.humanSub}>
				<div className="grid gap-4 p-4">
					<SelectControl
						name="triggers.offer_after_misses"
						label={t.handoff.offerAfterMisses}
						hint={t.handoff.offerAfterMissesHint}
						numeric
						options={MISS_VALUES.map((n) => ({
							value: String(n),
							label:
								n === 0 ? t.handoff.missesNever : n === 1 ? t.handoff.missesOne : fill(t.handoff.missesMany, { n }),
						}))}
					/>
					<HumanButtonSwitch />
				</div>
			</Panel>
			{/* /TV1 human */}
			<Panel title={t.handoff.triggersTitle}>
				<div className="grid gap-4 p-4">
					<SwitchControl name="triggers.explicit_request" label={t.handoff.explicit} />
					<SwitchControl name="triggers.frustration" label={t.handoff.frustration} />
					<OptionalNumberControl
						name="triggers.unresolved_turns"
						label={t.handoff.unresolved}
						numberLabel={t.handoff.unresolvedNumber}
						min={1}
						max={20}
						defaultValue={3}
					/>
					<OptionalNumberControl
						name="triggers.low_confidence_streak"
						label={t.handoff.lowConfidence}
						numberLabel={t.handoff.lowConfidenceNumber}
						min={1}
						max={10}
						defaultValue={2}
					/>
					<SwitchControl name="triggers.blocked_topics" label={t.handoff.blocked} />
					<SwitchControl name="triggers.out_of_hours" label={t.handoff.outOfHours} />
					<ChipsControl
						name="triggers.keywords"
						label={t.handoff.keywords}
						hint={t.handoff.keywordsHint}
						max={30}
						maxLength={60}
						addLabel={t.handoff.keywordsAdd}
					/>
				</div>
			</Panel>
			<Panel title={t.handoff.messagesTitle}>
				<div className="grid gap-4 p-4">
					<LocalizedTextField name="messages.open" label={t.handoff.open} max={300} rows={2} languages={languages} />
					<LocalizedTextField
						name="messages.closed"
						label={t.handoff.closed}
						max={300}
						rows={2}
						languages={languages}
					/>
					<LocalizedTextField
						name="messages.paused"
						label={t.handoff.paused}
						hint={t.handoff.pausedHint}
						max={300}
						rows={2}
						languages={languages}
					/>
					{/* PIP1 lane */}
					<LocalizedTextField
						name="messages.busy"
						label={t.handoff.busy}
						hint={t.handoff.busyHint}
						max={300}
						rows={2}
						languages={languages}
					/>
					{/* /PIP1 lane */}
				</div>
			</Panel>
			<Panel title={t.handoff.notifyTitle}>
				<div className="p-4">
					<NotifyFields />
				</div>
			</Panel>
		</SectionShell>
	);
}
