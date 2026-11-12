import { useQuery } from "@tanstack/react-query";
import { getRouteApi, Link } from "@tanstack/react-router";
import { Wand2 } from "lucide-react";
import { type ReactNode, useId } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { setupStrings } from "@/lib/strings/setup";
import { cn } from "@/lib/utils";
import type { CorpusDetail } from "../kbs/api";
import { assistantStateQuery } from "./api";
import { AssistantDirtyProvider } from "./dirty";
import { PublishBar } from "./PublishBar";
import { PreviewConsole } from "./preview/PreviewConsole";
import { AdvancedSection } from "./sections/AdvancedSection";
import { ConversationSection } from "./sections/ConversationSection";
import { HandoffSection } from "./sections/HandoffSection";
import { IdentitySection } from "./sections/IdentitySection";
import { LanguageSection } from "./sections/LanguageSection";
import { LeadsSection } from "./sections/LeadsSection";
import { OverviewSection } from "./sections/OverviewSection";
import { RulesSection } from "./sections/RulesSection";
import { ScopeSection } from "./sections/ScopeSection";
import { VersionsSection } from "./sections/VersionsSection";
import { VoiceSection } from "./sections/VoiceSection";
import { TestsSummary } from "./tests/TestsSummary";
import { ASSISTANT_SECTIONS, type AssistantSection, type AssistantState } from "./types";
import { hasConfig } from "./useDraftSection";

const route = getRouteApi("/_app/kbs/$kbId");

/** Sections that need a configuration to edit; without one the overview offers a template. */
function sectionBody(section: AssistantSection, kb: CorpusDetail, state: AssistantState): ReactNode {
	if (section === "overview" || !hasConfig(state)) return <OverviewSection kb={kb} state={state} />;
	const bodies: Record<AssistantSection, ReactNode> = {
		overview: <OverviewSection kb={kb} state={state} />,
		identity: <IdentitySection kb={kb} state={state} />,
		voice: <VoiceSection kb={kb} state={state} />,
		language: <LanguageSection kb={kb} state={state} />,
		scope: <ScopeSection kb={kb} state={state} />,
		rules: <RulesSection kb={kb} state={state} />,
		conversation: <ConversationSection kb={kb} state={state} />,
		leads: <LeadsSection kb={kb} state={state} />,
		handoff: <HandoffSection kb={kb} state={state} />,
		advanced: <AdvancedSection kb={kb} state={state} />,
		versions: <VersionsSection kb={kb} state={state} />,
		tests: <TestsSummary kb={kb.id} />,
		preview: <PreviewConsole kb={kb} state={state} />,
	};
	return bodies[section];
}

/** KB, Assistant tab (design section 9): sections in `?section=`, every form saves the draft. */
export function AssistantTab({ kb }: { kb: CorpusDetail }) {
	const { section = "overview" } = route.useSearch();
	const navigate = route.useNavigate();
	const selectId = useId();
	const q = useQuery(assistantStateQuery(kb.id));
	if (q.isPending) return <CardSkeleton lines={4} />;
	if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
	const state = q.data;
	const go = (s: AssistantSection) => void navigate({ search: (prev) => ({ ...prev, tab: "assistant", section: s }) });
	return (
		<AssistantDirtyProvider>
			<div className="grid gap-4">
				<div className="flex justify-end">
					<Button variant="outline" asChild>
						<Link to="/kbs/$kbId/setup" params={{ kbId: kb.id }}>
							<Wand2 aria-hidden />
							{setupStrings.tab.guidedSetup}
						</Link>
					</Button>
				</div>
				<PublishBar kb={kb.id} state={state} onPreview={() => go("preview")} />
				<div className="grid items-start gap-4 min-[861px]:grid-cols-[200px_minmax(0,1fr)]">
					<nav aria-label={t.tab.navLabel} className="hidden min-[861px]:block">
						<ul className="m-0 grid list-none gap-0.5 p-0">
							{ASSISTANT_SECTIONS.map((s) => (
								<li key={s}>
									<Link
										to="."
										search={(prev: Record<string, unknown>) => ({ ...prev, tab: "assistant", section: s })}
										aria-current={s === section ? "page" : undefined}
										className={cn(
											"block rounded-md px-3 py-1.5 text-[13.5px] font-medium text-foreground-2 hover:bg-secondary",
											s === section && "bg-secondary text-foreground",
										)}
									>
										{t.sections[s]}
									</Link>
								</li>
							))}
						</ul>
					</nav>
					<div className="min-[861px]:hidden">
						<div className="grid gap-1">
							<label htmlFor={selectId} className="text-[13px] font-semibold">
								{t.tab.sectionLabel}
							</label>
							<NativeSelect id={selectId} value={section} onChange={(e) => go(e.target.value as AssistantSection)}>
								{ASSISTANT_SECTIONS.map((s) => (
									<option key={s} value={s}>
										{t.sections[s]}
									</option>
								))}
							</NativeSelect>
						</div>
					</div>
					<div className="min-w-0" key={section}>
						{sectionBody(section, kb, state)}
					</div>
				</div>
			</div>
		</AssistantDirtyProvider>
	);
}
