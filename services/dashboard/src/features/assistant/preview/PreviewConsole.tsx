import { RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { sourceDomId } from "@/features/playground/SourcesPanel";
import { setActive, useKbSession } from "@/features/playground/session";
import { usePrincipal } from "@/lib/auth/store";
import { can } from "@/lib/permissions";
import { assistantStrings as t } from "@/lib/strings/assistant";
import type { CorpusDetail } from "../../kbs/api";
import { Panel } from "../../kbs/ui";
import type { AssistantState } from "../types";
import { currentConfig, hasConfig } from "../useDraftSection";
import { PreviewChat } from "./PreviewChat";
import { SimulateControls } from "./SimulateControls";
import { endPreview, NO_SIMULATION, previewKey, type Simulate } from "./stream";
import { TracePanel } from "./TracePanel";

/**
 * The preview console: chat with the draft the way a visitor would, pretend a page, an office
 * state or a language, and (owners) see how each answer was made. A preview turn is a real
 * question: it counts toward the cap and spends the model like any answer. Leaving the page or
 * starting a new chat drops the test conversation.
 */
export function PreviewConsole({ kb, state }: { kb: CorpusDetail; state: AssistantState }) {
	const principal = usePrincipal();
	const owner = can(principal, "assistant.debug");
	const key = previewKey(kb.id);
	const session = useKbSession(key);
	const [simulate, setSimulate] = useState<Simulate>(NO_SIMULATION);
	const [highlight, setHighlight] = useState<{ id: string; index: number } | null>(null);
	const languages = hasConfig(state) ? currentConfig(state).language.supported : ["en" as const];

	useEffect(() => () => endPreview(kb.id), [kb.id]);

	const active = session.messages.find((m) => m.id === session.activeId && m.role === "bot");
	const msg = active?.role === "bot" ? active : undefined;

	const pick = (messageId: string, index: number) => {
		setActive(key, messageId);
		setHighlight({ id: messageId, index });
		requestAnimationFrame(() => document.getElementById(sourceDomId(`trace-${messageId}`, index))?.focus());
	};

	return (
		<div className="grid gap-4">
			<Panel
				title={t.preview.title}
				sub={t.preview.sub}
				aside={
					<Button
						variant="outline"
						size="sm"
						onClick={() => {
							endPreview(kb.id);
							setHighlight(null);
						}}
					>
						<RotateCcw aria-hidden />
						{t.preview.newChat}
					</Button>
				}
			>
				<div className="grid gap-1 p-4 text-[12.5px] text-muted-foreground">
					<span>{state.draft ? t.preview.draftNote : t.preview.liveNote}</span>
					<span>{t.preview.costNote}</span>
				</div>
			</Panel>
			<details className="rounded-lg border border-border bg-card">
				<summary className="cursor-pointer px-4 py-3 text-[14px] font-bold">{t.preview.simulate}</summary>
				<p className="m-0 px-4 text-[12.5px] text-muted-foreground">{t.preview.simulateSub}</p>
				<SimulateControls value={simulate} onChange={setSimulate} languages={languages} />
			</details>
			<div className="grid items-start gap-4 min-[1101px]:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
				<PreviewChat kb={kb} debug={owner} simulate={simulate} onPick={pick} />
				<TracePanel
					msg={msg}
					owner={owner}
					highlight={highlight && highlight.id === msg?.id ? highlight.index : null}
				/>
			</div>
		</div>
	);
}
