import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { SettingsGrid } from "@/features/kbs/ui";
import { formatRelative } from "@/lib/format/date";
import { brandingStrings as t } from "@/lib/strings/admin";
import { fill } from "@/lib/strings/en";
import { type Settings, type SignupMode, settingsQuery } from "./api";
import { BrandForm } from "./BrandForm";
import { BrandPreview, type PreviewView } from "./BrandPreview";
import { type BrandValues, brandValues } from "./brand-form";
import { DisclosureCard } from "./DisclosureCard"; // TV5
import { HidePoweredByCard } from "./HidePoweredByCard";
import { NoticesCard } from "./NoticesCard"; // TV3
import { SignupModeCard } from "./SignupModeCard";
import { TrustCard } from "./TrustCard"; // TV1 human
import { WhatsAppRatesCard } from "./WhatsAppRatesCard"; // TV6

/** `/admin/branding` (mockup `[data-view=branding]`): sign-up mode, brand and a live preview. */
export function BrandingPage() {
	const q = useQuery(settingsQuery);
	return (
		<>
			<PageHeader
				title={t.page.title}
				description={t.page.lead}
				actions={
					q.data?.updated_at ? (
						<span className="self-end text-[12.5px] text-muted-foreground">
							{fill(t.page.lastChanged, {
								when: formatRelative(q.data.updated_at),
								who: q.data.updated_by ?? "—",
							})}
						</span>
					) : undefined
				}
			/>
			{q.isPending ? (
				<SettingsGrid>
					<div className="grid gap-4">
						<CardSkeleton lines={3} />
						<CardSkeleton lines={6} />
					</div>
					<CardSkeleton lines={6} />
				</SettingsGrid>
			) : q.isError ? (
				<ErrorState error={q.error} title={t.page.error} onRetry={() => void q.refetch()} />
			) : (
				<Editor settings={q.data} />
			)}
		</>
	);
}

function Editor({ settings }: { settings: Settings }) {
	// Keyed on the content: saving only the mode returns an equal branding, which must not reset
	// brand edits in progress.
	const brandKey = JSON.stringify(settings.branding);
	const initial = useMemo(() => brandValues(JSON.parse(brandKey) as Settings["branding"]), [brandKey]);
	const [values, setValues] = useState<BrandValues>(initial);
	const [mode, setMode] = useState<SignupMode>(settings.signup_mode);
	const [view, setView] = useState<PreviewView>(settings.signup_mode === "closed" ? "closed" : "login");
	// A save (or another tab) brought new server values: start from them.
	useEffect(() => setValues(initial), [initial]);
	useEffect(() => setMode(settings.signup_mode), [settings.signup_mode]);

	return (
		<SettingsGrid>
			<div className="grid min-w-0 content-start gap-4">
				<SignupModeCard
					saved={settings.signup_mode}
					cobPublic={settings.cob_public_sign_in}
					value={mode}
					onChange={(m) => {
						setMode(m);
						setView(m === "closed" ? "closed" : "login");
					}}
				/>
				{/* TV1 human */}
				<TrustCard allowHideHumanButton={settings.trust?.allow_hide_human_button === true} />
				{/* /TV1 human */}
				{/* TV5 */}
				<DisclosureCard
					key={JSON.stringify([settings.trust?.require_ai_disclosure, settings.trust?.disclosure_wording_editable])}
					require={settings.trust?.require_ai_disclosure !== false}
					editable={settings.trust?.disclosure_wording_editable !== false}
				/>
				{/* /TV5 */}
				{/* TV3 */}
				<NoticesCard key={JSON.stringify(settings.trust ?? {})} trust={settings.trust} />
				{/* /TV3 */}
				{/* TV6 whatsapp */}
				<WhatsAppRatesCard key={JSON.stringify(settings.whatsapp ?? {})} whatsapp={settings.whatsapp} />
				{/* /TV6 whatsapp */}
				<BrandForm initial={initial} onChange={setValues} />
				<HidePoweredByCard hidden={settings.branding.hide_powered_by === true} />
			</div>
			<div className="min-w-0 min-[1101px]:sticky min-[1101px]:top-4">
				<BrandPreview values={values} view={view} onView={setView} />
			</div>
		</SettingsGrid>
	);
}
