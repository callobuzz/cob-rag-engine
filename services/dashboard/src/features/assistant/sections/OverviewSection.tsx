import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { EmptyState } from "@/components/patterns/EmptyState";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Button } from "@/components/ui/button";
import { toApiError } from "@/lib/api/errors";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { fill } from "@/lib/strings/en";
import type { CorpusDetail } from "../../kbs/api";
import { PromptTab } from "../../kbs/detail/PromptTab";
import { KbErrorAlert } from "../../kbs/KbErrorAlert";
import { Panel } from "../../kbs/ui";
import { useSaveDraft, useSetEnabled } from "../api";
import { tabLangs } from "../defaults";
import { ASSISTANT_SECTIONS, type AssistantState } from "../types";
import { currentConfig, hasConfig, liveEnabled } from "../useDraftSection";
import { TemplatePicker } from "./TemplatePicker";

const EXTRA_MAX = 15_000;

/** The classic prompt fields joined the way the engine's default config joins them. */
export function classicText(kb: CorpusDetail): string {
	const c = (kb.prompt_config ?? {}) as Record<string, unknown>;
	return [c.system_prompt, c.constraints]
		.filter((x): x is string => typeof x === "string" && x.trim() !== "")
		.map((x) => x.trim())
		.join("\n\n");
}

/** The summary, the opt-out switch and (while the assistant is off) the classic instructions. */
export function OverviewSection({ kb, state }: { kb: CorpusDetail; state: AssistantState }) {
	const [picker, setPicker] = useState(false);
	const [confirmOff, setConfirmOff] = useState(false);
	const [error, setError] = useState<unknown>(null);
	const setEnabled = useSetEnabled(kb.id);
	const save = useSaveDraft(kb.id);

	const toggle = async (enabled: boolean) => {
		setError(null);
		try {
			const out = await setEnabled.mutateAsync(enabled);
			setConfirmOff(false);
			if (out.version === state.published?.version) toast(t.overview.unchangedToast);
			else toast.success(enabled ? t.overview.onToast : t.overview.offToast);
		} catch (e) {
			setConfirmOff(false);
			setError(toApiError(e));
		}
	};

	if (!hasConfig(state)) {
		return (
			<>
				<EmptyState
					title={t.overview.emptyTitle}
					body={t.overview.emptyBody}
					primaryAction={<Button onClick={() => setPicker(true)}>{t.overview.chooseTemplate}</Button>}
				/>
				<TemplatePicker kb={kb.id} state={state} open={picker} onOpenChange={setPicker} />
			</>
		);
	}

	const config = currentConfig(state);
	const on = state.enabled;
	const classic = classicText(kb);
	const card = config.identity.card;
	const cardSet = !!(card.phone || card.whatsapp || card.email || card.hours.length);

	const copyClassic = async () => {
		const existing = config.advanced.extra_instructions?.trim() ?? "";
		const joined = [existing, classic].filter(Boolean).join("\n\n");
		if (!classic) return void toast(t.overview.copyClassicNone);
		if (joined.length > EXTRA_MAX) return void toast.error(t.overview.copyClassicTooLong);
		setError(null);
		try {
			await save.mutateAsync({
				config: { ...config, enabled: liveEnabled(state, config), advanced: { extra_instructions: joined } },
				base_revision: state.draft?.draft_revision ?? 0,
			});
			toast.success(t.overview.copiedToast);
		} catch (e) {
			setError(toApiError(e));
		}
	};

	return (
		<div className="grid gap-4">
			{error ? <KbErrorAlert error={error} /> : null}
			{!on ? (
				<FormAlert tone="warn">
					<b className="font-semibold">{t.overview.offBanner}</b>
					<span className="mt-2 block">
						<OwnerGate action="assistant.publish">
							<Button size="sm" disabled={setEnabled.isPending} onClick={() => void toggle(true)}>
								{t.overview.backOn}
							</Button>
						</OwnerGate>
					</span>
				</FormAlert>
			) : null}
			<Panel
				title={t.overview.visitorsSee}
				aside={
					<Button variant="outline" size="sm" onClick={() => setPicker(true)}>
						{t.overview.startFromTemplate}
					</Button>
				}
			>
				<dl className="m-0 grid gap-3 p-4 text-[13.5px] min-[700px]:grid-cols-2">
					<Item label={t.overview.name}>{config.identity.assistant_name}</Item>
					<Item label={t.overview.templateInUse}>{t.templateNames[config.identity.business_type]}</Item>
					<Item label={t.overview.greeting}>
						<span className="grid gap-0.5">
							{(["en", "hi"] as const).map((l) =>
								config.voice.greeting[l] ? (
									<span key={l} lang={l}>
										{config.voice.greeting[l]}
									</span>
								) : null,
							)}
						</span>
					</Item>
					<Item label={t.overview.languages}>
						{tabLangs(config.language.supported)
							.map((l) => t.langs[l])
							.join(", ")}
					</Item>
					<Item label={t.overview.scopeMode}>{t.scopeModes[config.scope.scope_mode]}</Item>
					<Item label={t.overview.cardState}>{cardSet ? t.overview.cardSet : t.overview.cardEmpty}</Item>
				</dl>
				<nav aria-label={t.overview.goTo} className="flex flex-wrap gap-1.5 border-t border-border px-4 py-3">
					{ASSISTANT_SECTIONS.filter((s) => s !== "overview").map((s) => (
						<Link
							key={s}
							to="."
							search={(prev: Record<string, unknown>) => ({ ...prev, tab: "assistant", section: s })}
							className="rounded-md border border-border bg-secondary px-2.5 py-1 text-[12.5px] font-semibold text-foreground-2 hover:bg-accent"
						>
							{t.sections[s]}
						</Link>
					))}
				</nav>
			</Panel>
			{on ? (
				<Panel title={t.overview.classicTitle}>
					<section aria-label={t.overview.classicTitle} className="grid gap-2 p-4">
						<p className="m-0 text-[13.5px] font-medium">{t.overview.classicUnused}</p>
						<p className="m-0 text-[12.5px] text-muted-foreground">{t.overview.classicUnusedBody}</p>
						{classic ? (
							<div>
								<Button variant="outline" size="sm" disabled={save.isPending} onClick={() => void copyClassic()}>
									{t.overview.copyClassic}
								</Button>
							</div>
						) : null}
					</section>
				</Panel>
			) : (
				<section aria-label={t.overview.classicTitle}>
					<PromptTab kb={kb} />
				</section>
			)}
			{on ? (
				<Panel title={t.overview.offTitle} tone="danger">
					<div className="grid gap-3 p-4">
						<p className="m-0 text-[13.5px] text-foreground-2">{t.overview.offBody}</p>
						<div>
							<OwnerGate action="assistant.publish">
								<Button variant="destructive" disabled={setEnabled.isPending} onClick={() => setConfirmOff(true)}>
									{t.overview.offTitle}
								</Button>
							</OwnerGate>
						</div>
					</div>
				</Panel>
			) : null}
			<TemplatePicker kb={kb.id} state={state} open={picker} onOpenChange={setPicker} />
			<ConfirmDialog
				open={confirmOff}
				onOpenChange={setConfirmOff}
				title={t.overview.offConfirmTitle}
				body={t.overview.offConfirmBody}
				confirmLabel={t.overview.offConfirm}
				pending={setEnabled.isPending}
				onConfirm={() => void toggle(false)}
			/>
			{state.draft ? (
				<p className="m-0 text-[12.5px] text-muted-foreground">
					{fill(t.overview.draftNote, { state: on ? t.overview.stateOn : t.overview.stateOff })}
				</p>
			) : null}
		</div>
	);
}

function Item({ label, children }: { label: string; children: React.ReactNode }) {
	return (
		<div className="grid gap-0.5">
			<dt className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</dt>
			<dd className="m-0">{children}</dd>
		</div>
	);
}
