import { useQuery } from "@tanstack/react-query";
import { Mail, MessageCircle } from "lucide-react";
import { useState } from "react";
import { CopyButton } from "@/components/patterns/CopyButton";
import { Button, buttonVariants } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { onboardingStrings } from "@/lib/strings/onboarding";
import { type Onboarding, onboardingQuery } from "./api";
import { ConnectionLight } from "./ConnectionLight";
import { developerMailto, developerWhatsapp, PLATFORMS, type Platform, siteName } from "./install-guides";

const t = onboardingStrings.two;

/**
 * Step 2: "Add your assistant to your website". One Copy button, steps for the owner's platform
 * (or a message to send to their developer, built in the browser: the engine sends nothing) and
 * the light that turns green when the assistant loads on the site. Next is always open: "I'll do
 * it later".
 */
export function InstallStep({
	state: initial,
	onNext,
	onBack,
}: {
	state: Onboarding;
	onNext: () => void;
	onBack: () => void;
}) {
	// The light below keeps this query fresh; reading it here updates the button when it turns green.
	const state = useQuery({ ...onboardingQuery(false), initialData: initial }).data ?? initial;
	const [platform, setPlatform] = useState<Platform>("any");
	const snippet = state.snippet;
	if (!snippet) {
		return (
			<div className="grid gap-3">
				<p className="m-0 text-[13.5px] text-foreground-2">{onboardingStrings.one.lead}</p>
				<div>
					<Button onClick={onBack}>{onboardingStrings.back}</Button>
				</div>
			</div>
		);
	}
	return (
		<div className="grid gap-5">
			<div>
				<h2 className="text-lg font-bold">{t.title}</h2>
				<p className="m-0 text-[13.5px] text-foreground-2">{t.lead}</p>
			</div>
			<div className="grid gap-2">
				<div className="flex flex-wrap items-center justify-between gap-2">
					<span className="text-sm font-semibold">{t.codeLabel}</span>
					<CopyButton value={snippet} label={t.copy} ariaLabel={t.copy} />
				</div>
				<pre
					data-testid="install-snippet"
					className="m-0 overflow-x-auto rounded-md border border-border bg-muted p-3 font-mono text-[12.5px] break-all whitespace-pre-wrap"
				>
					{snippet}
				</pre>
			</div>
			<Tabs value={platform} onValueChange={(v) => setPlatform(v as Platform)}>
				<span className="text-sm font-semibold">{t.platformsLabel}</span>
				<div className="overflow-x-auto">
					<TabsList aria-label={t.platformsLabel}>
						{PLATFORMS.map((p) => (
							<TabsTrigger key={p} value={p}>
								{t.platforms[p]}
							</TabsTrigger>
						))}
					</TabsList>
				</div>
				{PLATFORMS.filter((p) => p !== "developer").map((p) => (
					<TabsContent key={p} value={p}>
						<ol className="m-0 grid gap-1.5 pl-5 text-[13.5px]">
							{(t.guides[p as Exclude<Platform, "developer">] ?? []).map((step) => (
								<li key={step}>{step}</li>
							))}
						</ol>
					</TabsContent>
				))}
				<TabsContent value="developer">
					<div className="grid gap-3">
						<p className="m-0 text-[13.5px] text-foreground-2">{t.developer.intro}</p>
						<div className="flex flex-wrap gap-2">
							<a
								href={developerMailto(snippet, siteName(state.website))}
								className={buttonVariants({ variant: "outline", size: "sm" })}
							>
								<Mail aria-hidden />
								{t.developer.email}
							</a>
							<a
								href={developerWhatsapp(snippet, siteName(state.website))}
								target="_blank"
								rel="noreferrer"
								className={buttonVariants({ variant: "outline", size: "sm" })}
							>
								<MessageCircle aria-hidden />
								{t.developer.whatsapp}
							</a>
						</div>
					</div>
				</TabsContent>
			</Tabs>
			<ConnectionLight state={state} />
			<div className="flex flex-wrap justify-between gap-2">
				<Button variant="ghost" onClick={onBack}>
					{onboardingStrings.back}
				</Button>
				<Button onClick={onNext}>{state.connection.status === "connected" ? t.next : t.later}</Button>
			</div>
		</div>
	);
}
