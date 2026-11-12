import { useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { ExternalLink, KeyRound, ServerCog, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { FormAlert } from "@/components/auth/FormAlert";
import { KbScopedPage } from "@/components/patterns/KbScopedPage";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { PageHeader } from "@/components/shell/PageHeader";
import { buttonVariants } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { type Corpus, infoQuery } from "@/lib/api/queries";
import { useRagUrl } from "@/lib/auth/store";
import { fill } from "@/lib/strings/en";
import { agentsStrings as t } from "@/lib/strings/widget";
import { CodeBlock, ConfigTabs } from "./ConfigTabs";
import { ConnectAgent, type ConnectedAgent } from "./ConnectAgent";
import {
	agentEngineUrl,
	DEFAULT_INSTALL,
	type InstallMode,
	isLoopback,
	KEY_PLACEHOLDER,
	LOCAL_INSTALL_STEPS,
	MCP_INSTALL_DOC,
	MCP_NPM,
	MCP_README,
} from "./configs";

const route = getRouteApi("/_app/agents");

/** `/agents?kb=&client=` (D6): copy-ready cob-rag-mcp configs for this engine. No API calls but /info. */
export function AgentsPage() {
	return (
		<KbScopedPage title={t.page.title} emptyBody={t.page.noKbBody}>
			{(kb) => <Agents key={kb.id} kb={kb} />}
		</KbScopedPage>
	);
}

function Agents({ kb }: { kb: Corpus }) {
	const { client = "claude-code" } = route.useSearch();
	const navigate = useNavigate();
	const [install, setInstall] = useState<InstallMode>(DEFAULT_INSTALL);
	const [connected, setConnected] = useState<ConnectedAgent | null>(null);
	const ragUrl = useRagUrl();
	const info = useQuery(infoQuery);
	const url = agentEngineUrl(info.data, ragUrl);
	const loopback = isLoopback(url);
	const host = (() => {
		try {
			return new URL(url).hostname;
		} catch {
			return url;
		}
	})();

	return (
		<>
			<PageHeader title={t.page.title} description={fill(t.page.lead, { kb: kb.name })} />
			<div className="mb-4">
				<ConnectAgent
					kb={kb}
					clientLabel={t.clients[client].label}
					install={install}
					onInstall={setInstall}
					connected={connected}
					onConnected={setConnected}
					onForget={() => setConnected(null)}
				/>
			</div>
			<div className="grid items-start gap-4 min-[1101px]:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
				<Panel title={t.clients[client].label} sub={t.page.requirements}>
					<div className="grid gap-4 p-4">
						<div className="grid gap-1">
							<span className="text-[13px] font-semibold">{t.page.engineUrl}</span>
							<code className="font-mono text-[13px] break-all" data-testid="engine-url">
								{url}
							</code>
							<small className="text-[12.5px] text-muted-foreground">{t.page.engineUrlHint}</small>
						</div>
						{loopback ? (
							<FormAlert tone="warn">
								<span data-testid="loopback-note">{fill(t.page.loopback, { host })}</span>
							</FormAlert>
						) : null}
						{install === "local" ? (
							<div className="grid gap-2" data-testid="local-install">
								<b className="text-[13px] font-semibold">{t.connect.installTitle}</b>
								<p className="m-0 text-[13px] text-foreground-2">{t.connect.installLead}</p>
								<CodeBlock
									code={LOCAL_INSTALL_STEPS.join("\n")}
									label={t.connect.installTitle}
									copyLabel={`Copy ${t.connect.installTitle}`}
								/>
								<p className="m-0 flex flex-wrap items-center gap-x-3 text-[12.5px] text-muted-foreground">
									<span>{t.connect.installAfter}</span>
									<a
										href={MCP_INSTALL_DOC}
										target="_blank"
										rel="noopener noreferrer"
										className="inline-flex items-center gap-1 font-semibold"
									>
										{t.connect.installDoc}
										<ExternalLink className="size-3.5" aria-hidden />
									</a>
								</p>
							</div>
						) : null}
						<ConfigTabs
							url={url}
							client={client}
							onClient={(c) => void navigate({ to: "/agents", search: { kb: kb.id, client: c }, replace: true })}
							opts={{
								install,
								key: connected?.key,
								corpusId: connected?.target === "customer" ? kb.id : undefined,
							}}
						/>
						{connected ? null : (
							<p className="m-0 flex items-start gap-2 text-[12.5px] text-muted-foreground">
								<KeyRound className="mt-0.5 size-3.5 shrink-0" aria-hidden />
								<span>
									{t.page.keyPlaceholderNote.split(KEY_PLACEHOLDER)[0]}
									<code className="font-mono">{KEY_PLACEHOLDER}</code>
									{t.page.keyPlaceholderNote.split(KEY_PLACEHOLDER)[1]}
								</span>
							</p>
						)}
					</div>
				</Panel>
				<div className="grid min-w-0 gap-4">
					<Panel title={t.key.title} sub={t.key.sub}>
						<div className="grid gap-3 p-4">
							<ul className="m-0 grid list-none gap-2.5 p-0 text-[13.5px]">
								<li className="rounded-md border border-border bg-muted px-3 py-2.5">
									<b>{t.key.ckLabel}</b> {t.key.ck}
								</li>
								<li className="rounded-md border border-border bg-muted px-3 py-2.5">
									<b>{t.key.tkLabel}</b> {fill(t.key.tk, { id: kb.id })}
								</li>
							</ul>
							<div className="flex flex-wrap items-center gap-3">
								<OwnerGate action="key.manage">
									<Link
										to="/kbs/$kbId"
										params={{ kbId: kb.id }}
										search={{ tab: "key" }}
										className={buttonVariants({ variant: "default", size: "sm" })}
									>
										<KeyRound aria-hidden />
										{t.key.get}
									</Link>
								</OwnerGate>
								<small className="text-[12.5px] text-muted-foreground">{t.key.getHint}</small>
							</div>
						</div>
					</Panel>
					<Panel title={t.tools.title} sub={t.tools.sub}>
						<div className="grid gap-3 p-4">
							<ul className="m-0 grid list-none gap-2 p-0">
								{t.tools.items.map((i) => (
									<li key={i.b} className="flex items-start gap-2.5 text-[13.5px]">
										<ServerCog className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
										<span>
											<code className="font-mono text-[12.5px] font-semibold">{i.b}</code>
											<span className="text-foreground-2"> — {i.t}</span>
										</span>
									</li>
								))}
							</ul>
							<p className="m-0 flex items-start gap-2 text-[12.5px] text-foreground-2">
								<TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
								<span>{t.tools.destructive}</span>
							</p>
							<div className="flex flex-wrap items-center gap-x-4 gap-y-1">
								<a
									href={MCP_README}
									target="_blank"
									rel="noopener noreferrer"
									className="inline-flex items-center gap-1 text-[13px] font-semibold"
								>
									{t.tools.readme}
									<ExternalLink className="size-3.5" aria-hidden />
								</a>
								<a
									href={MCP_NPM}
									target="_blank"
									rel="noopener noreferrer"
									className="inline-flex items-center gap-1 text-[13px] font-semibold"
								>
									{t.tools.npm}
									<ExternalLink className="size-3.5" aria-hidden />
								</a>
							</div>
						</div>
					</Panel>
				</div>
			</div>
		</>
	);
}
