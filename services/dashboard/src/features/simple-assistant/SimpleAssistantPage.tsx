import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Database, SlidersHorizontal } from "lucide-react";
import type { ReactNode } from "react";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { buttonVariants } from "@/components/ui/button";
import { assistantStateQuery } from "@/features/assistant/api";
import { AssistantDirtyProvider } from "@/features/assistant/dirty";
import { PublishBar } from "@/features/assistant/PublishBar";
import { HandoffSection } from "@/features/assistant/sections/HandoffSection";
import { IdentitySection } from "@/features/assistant/sections/IdentitySection";
import { LanguageSection } from "@/features/assistant/sections/LanguageSection";
import { OverviewSection } from "@/features/assistant/sections/OverviewSection";
import { VoiceSection } from "@/features/assistant/sections/VoiceSection";
import { hasConfig } from "@/features/assistant/useDraftSection";
import { corpusQuery } from "@/features/kbs/api";
import { AssistantAvatar } from "@/features/onboarding/AssistantAvatar"; // BIZ4
import { useMainKb } from "@/lib/main-kb";
import { onboardingStrings as ob } from "@/lib/strings/onboarding"; // BIZ4
import { simpleAssistantStrings as t } from "@/lib/strings/simple-assistant";

/**
 * `/assistant` for a business (BIZ3): the knowledge base's assistant in four plain parts, the same
 * forms the knowledge base's Assistant tab uses. "All settings" opens the full tab. A knowledge
 * base with no assistant yet shows the template picker, exactly like the tab does.
 */
export function SimpleAssistantPage() {
	const main = useMainKb();
	if (main.isLoading) {
		return (
			<div className="grid gap-4" aria-busy="true">
				<PageHeader title={t.title} description={t.lead} />
				<CardSkeleton lines={3} />
			</div>
		);
	}
	if (main.isError) {
		return (
			<div className="grid gap-4">
				<PageHeader title={t.title} description={t.lead} />
				<ErrorState error={main.error} title={t.loadError} onRetry={main.refetch} />
			</div>
		);
	}
	if (!main.kbId) {
		return (
			<div className="grid gap-4">
				<PageHeader title={t.title} description={t.lead} />
				<EmptyState
					title={t.noKbTitle}
					body={t.noKbBody}
					primaryAction={
						<Link to="/kbs" search={{ new: 1 }} className={buttonVariants({ variant: "default" })}>
							<Database aria-hidden />
							{t.noKbAction}
						</Link>
					}
				/>
			</div>
		);
	}
	return <SimpleAssistant key={main.kbId} kbId={main.kbId} />;
}

function SimpleAssistant({ kbId }: { kbId: string }) {
	const navigate = useNavigate();
	const kbq = useQuery(corpusQuery(kbId));
	const state = useQuery(assistantStateQuery(kbId));
	const header = (
		<PageHeader
			title={t.title}
			description={t.lead}
			actions={
				<Link
					to="/kbs/$kbId"
					params={{ kbId }}
					search={{ tab: "assistant" }}
					className={buttonVariants({ variant: "outline" })}
					title={t.allSettingsHint}
				>
					<SlidersHorizontal aria-hidden />
					{t.allSettings}
				</Link>
			}
		/>
	);
	if (kbq.isPending || state.isPending) {
		return (
			<div className="grid gap-4" aria-busy="true">
				{header}
				<CardSkeleton lines={4} />
			</div>
		);
	}
	const error = kbq.error ?? state.error;
	if (error || !kbq.data || !state.data) {
		return (
			<div className="grid gap-4">
				{header}
				<ErrorState
					error={error}
					title={t.loadError}
					onRetry={() => {
						void kbq.refetch();
						void state.refetch();
					}}
				/>
			</div>
		);
	}
	const kb = kbq.data;
	const s = state.data;
	return (
		<AssistantDirtyProvider>
			<div className="grid gap-5">
				{header}
				{hasConfig(s) ? (
					<>
						<PublishBar
							kb={kbId}
							state={s}
							onPreview={() =>
								void navigate({ to: "/kbs/$kbId", params: { kbId }, search: { tab: "assistant", section: "preview" } })
							}
						/>
						{/* BIZ4: the assistant's picture, uploaded and put in the draft */}
						<Part id="avatar" title={ob.avatarPage.title} help={ob.avatarPage.help}>
							<AssistantAvatar kb={kbId} state={s} />
						</Part>
						{/* /BIZ4 */}
						<Part id="identity" title={t.sections.identity.title} help={t.sections.identity.help}>
							<IdentitySection kb={kb} state={s} />
						</Part>
						<Part id="voice" title={t.sections.voice.title} help={t.sections.voice.help}>
							<VoiceSection kb={kb} state={s} />
						</Part>
						<Part id="language" title={t.sections.language.title} help={t.sections.language.help}>
							<LanguageSection kb={kb} state={s} />
						</Part>
						<Part id="handoff" title={t.sections.handoff.title} help={t.sections.handoff.help}>
							<HandoffSection kb={kb} state={s} />
						</Part>
					</>
				) : (
					<OverviewSection kb={kb} state={s} />
				)}
			</div>
		</AssistantDirtyProvider>
	);
}

function Part({ id, title, help, children }: { id: string; title: string; help: string; children: ReactNode }) {
	return (
		<section aria-labelledby={`simple-${id}`} className="grid gap-2" data-testid={`simple-${id}`}>
			<div>
				<h2 id={`simple-${id}`} className="text-lg font-bold">
					{title}
				</h2>
				<p className="m-0 text-[13.5px] text-foreground-2">{help}</p>
			</div>
			{children}
		</section>
	);
}
