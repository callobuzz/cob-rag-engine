import type { ReactNode } from "react";
import { CopyButton } from "@/components/patterns/CopyButton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AGENT_CLIENTS, type AgentClient } from "@/lib/search";
import { en, fill } from "@/lib/strings/en";
import { agentsStrings as t } from "@/lib/strings/widget";
import { CONFIG_FILES, type ConfigOptions, configFor, HTTP_ENDPOINT, mcpServersJsonWindowsCmd } from "./configs";

/** One focusable code block with a copy button labelled per client. */
export function CodeBlock({ code, label, copyLabel }: { code: string; label: string; copyLabel: string }) {
	return (
		<div className="grid gap-2">
			<div className="flex justify-end">
				<CopyButton value={code} label={en.common.copy} ariaLabel={copyLabel} />
			</div>
			<figure aria-label={label} className="m-0 min-w-0">
				<pre
					// biome-ignore lint/a11y/noNoninteractiveTabindex: scrollable code must be reachable by keyboard
					tabIndex={0}
					data-testid="config-code"
					className="m-0 overflow-x-auto rounded-md border border-border bg-muted p-3 font-mono text-[12.5px] leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere]"
				>
					{code}
				</pre>
			</figure>
		</div>
	);
}

const mono = (s: string) => <code className="font-mono text-[12.5px]">{s}</code>;

/** Replace `{x}` in a sentence with React nodes (file paths as code). */
function withCode(template: string, values: Record<string, string>): ReactNode {
	return template.split(/(\{\w+\})/g).map((part, i) => {
		const m = /^\{(\w+)\}$/.exec(part);
		const v = m?.[1] ? values[m[1]] : undefined;
		// biome-ignore lint/suspicious/noArrayIndexKey: static sentence parts
		return v !== undefined ? <span key={i}>{mono(v)}</span> : <span key={i}>{part}</span>;
	});
}

/** The five clients as tabs (`?client=`), each with its copy-ready config. */
export function ConfigTabs({
	url,
	client,
	onClient,
	opts = {},
}: {
	url: string;
	client: AgentClient;
	onClient: (c: AgentClient) => void;
	/** The key (a real one just made, else the placeholder), the install mode and a default knowledge base. */
	opts?: ConfigOptions;
}) {
	return (
		<Tabs value={client} onValueChange={(v) => onClient(v as AgentClient)} className="gap-3">
			<div className="-mx-1 overflow-x-auto px-1 pb-1">
				<TabsList aria-label={t.page.tabsLabel} className="h-auto! flex-wrap justify-start">
					{AGENT_CLIENTS.map((c) => (
						<TabsTrigger key={c} value={c} className="flex-none px-3 py-1.5">
							{t.clients[c].label}
						</TabsTrigger>
					))}
				</TabsList>
			</div>
			{AGENT_CLIENTS.map((c) => {
				const name = t.clients[c].label;
				const cfg = configFor(c, url, opts);
				const block = (
					<CodeBlock
						code={cfg.code}
						label={fill(t.page.codeLabel, { client: name })}
						copyLabel={fill(t.page.copyFor, { client: name })}
					/>
				);
				return (
					<TabsContent key={c} value={c} className="grid gap-3">
						{c === "claude-code" ? (
							<>
								<p className="m-0 text-[13.5px] text-foreground-2">{t.clients["claude-code"].lead}</p>
								{block}
								<p className="m-0 text-[12.5px] text-muted-foreground">
									{opts.install === "local" ? t.clients["claude-code"].afterLocal : t.clients["claude-code"].after}
								</p>
							</>
						) : null}
						{c === "claude-desktop" ? (
							<>
								<p className="m-0 text-[13.5px] text-foreground-2">{t.clients["claude-desktop"].lead}</p>
								<p className="m-0 text-[12.5px] text-muted-foreground">
									{withCode(t.clients["claude-desktop"].files, CONFIG_FILES.claudeDesktop)}
								</p>
								{block}
								<details className="text-[13px]">
									<summary className="cursor-pointer font-semibold text-foreground-2">
										{t.clients["claude-desktop"].windowsNpx}
									</summary>
									<div className="mt-2">
										<CodeBlock
											code={mcpServersJsonWindowsCmd(url, opts)}
											label={fill(t.page.codeLabel, { client: `${name} (Windows, cmd)` })}
											copyLabel={fill(t.page.copyFor, { client: `${name} Windows` })}
										/>
									</div>
								</details>
							</>
						) : null}
						{c === "cursor" ? (
							<>
								<p className="m-0 text-[13.5px] text-foreground-2">
									{withCode(t.clients.cursor.lead, CONFIG_FILES.cursor)}
								</p>
								{block}
							</>
						) : null}
						{c === "vscode" ? (
							<>
								<p className="m-0 text-[13.5px] text-foreground-2">
									{withCode(t.clients.vscode.lead, { file: CONFIG_FILES.vscode })}
								</p>
								{block}
								{opts.key ? <p className="m-0 text-[12.5px] text-muted-foreground">{t.connect.vscodeNote}</p> : null}
							</>
						) : null}
						{c === "n8n" ? (
							<>
								<p className="m-0 text-[13.5px] text-foreground-2">{t.clients.n8n.lead}</p>
								{block}
								<p className="m-0 text-[13.5px] text-foreground-2">
									{withCode(t.clients.n8n.node, { endpoint: HTTP_ENDPOINT })}
								</p>
								<p className="m-0 text-[12.5px] text-muted-foreground">
									{opts.key ? t.connect.n8nNote : t.clients.n8n.keyNote}
								</p>
							</>
						) : null}
					</TabsContent>
				);
			})}
		</Tabs>
	);
}
