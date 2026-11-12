import type { AgentClient } from "@/lib/search";

/**
 * Copy-ready cob-rag-mcp client configs (D6), exactly as the cob-rag-mcp README gives them
 * ("Connect an MCP client" and "Install before npm publish"), with this engine's URL filled in and
 * either a placeholder for the key or the real one a "Connect an agent" just made. Pure functions;
 * JSON is built with JSON.stringify so every value is escaped correctly.
 *
 * Two ways to run the server: `npx -y cob-rag-mcp start` (once the package is on npm) or the
 * `cob-rag-mcp start` command that `npm link` puts on the PATH from a local checkout (before it).
 * The environment variables are the same either way: COB_RAG_URL, COB_RAG_API_KEY and, for a
 * customer (`tk_`) key, COB_RAG_CORPUS_ID, the knowledge base the tools default to.
 */

export const KEY_PLACEHOLDER = "ck_your_knowledge_base_key";
export const SERVER_NAME = "cob-rag";
export const NPX_ARGS = ["-y", "cob-rag-mcp", "start"] as const;
/** The bin `npm link` (or `npm i -g`) installs. */
export const BIN = "cob-rag-mcp";
export const BIN_ARGS = ["start"] as const;
/** VS Code's input variable (literal text, not a template). */
// biome-ignore lint/suspicious/noTemplateCurlyInString: VS Code substitutes this itself
export const VSCODE_KEY_INPUT = "${input:cob-rag-key}";
export const HTTP_PORT = 3333;
export const HTTP_ENDPOINT = `http://127.0.0.1:${HTTP_PORT}/mcp`;
export const MCP_README = "https://github.com/callobuzz/cob-rag-mcp#tools";
export const MCP_NPM = "https://www.npmjs.com/package/cob-rag-mcp";
export const MCP_REPO = "https://github.com/callobuzz/cob-rag-mcp.git";
export const MCP_INSTALL_DOC = "https://github.com/callobuzz/cob-rag-mcp#install-before-npm-publish";

/** Flip to true once cob-rag-mcp is published to npm: `npx` becomes the default install. */
export const MCP_PUBLISHED = false;

export type InstallMode = "npx" | "local";
export const DEFAULT_INSTALL: InstallMode = MCP_PUBLISHED ? "npx" : "local";

export interface ConfigOptions {
	/** The real key, when one was just made; else the placeholder. */
	key?: string;
	/** `npx` (default here) or the `cob-rag-mcp` bin from a local checkout. */
	install?: InstallMode;
	/** A `tk_` key reaches every knowledge base of the customer: this one is the default. */
	corpusId?: string;
}

/** The git steps of README "Install before npm publish" (one line per command). */
export const LOCAL_INSTALL_STEPS = [
	`git clone ${MCP_REPO}`,
	"cd cob-rag-mcp",
	"pnpm install && pnpm build && npm link",
] as const;

/** Where each client reads its config (cob-rag-mcp README). */
export const CONFIG_FILES = {
	claudeDesktop: {
		mac: "~/Library/Application Support/Claude/claude_desktop_config.json",
		win: "%APPDATA%\\Claude\\claude_desktop_config.json",
	},
	cursor: { global: "~/.cursor/mcp.json", project: ".cursor/mcp.json" },
	vscode: ".vscode/mcp.json",
} as const;

const pretty = (v: unknown) => JSON.stringify(v, null, 2);

/** The command and its arguments for a way of installing. */
export function launcher(install: InstallMode = "npx"): { command: string; args: string[] } {
	return install === "local" ? { command: BIN, args: [...BIN_ARGS] } : { command: "npx", args: [...NPX_ARGS] };
}

function env(url: string, key: string, corpusId?: string): Record<string, string> {
	return { COB_RAG_URL: url, COB_RAG_API_KEY: key, ...(corpusId ? { COB_RAG_CORPUS_ID: corpusId } : {}) };
}

/** Claude Code: one command (a single line, so it pastes into bash, zsh, PowerShell and cmd). */
export function claudeCodeCommand(url: string, opts: ConfigOptions = {}): string {
	const { command, args } = launcher(opts.install);
	const corpus = opts.corpusId ? ` -e COB_RAG_CORPUS_ID=${opts.corpusId}` : "";
	return `claude mcp add ${SERVER_NAME} -e COB_RAG_URL=${url} -e COB_RAG_API_KEY=${opts.key ?? KEY_PLACEHOLDER}${corpus} -- ${command} ${args.join(" ")}`;
}

/** Claude Desktop and Cursor share the `mcpServers` shape. */
export function mcpServersJson(url: string, opts: ConfigOptions = {}): string {
	const { command, args } = launcher(opts.install);
	return pretty({
		mcpServers: { [SERVER_NAME]: { command, args, env: env(url, opts.key ?? KEY_PLACEHOLDER, opts.corpusId) } },
	});
}

/** README troubleshooting: "Windows: npx not found from Claude Desktop" → run it through cmd. */
export function mcpServersJsonWindowsCmd(url: string, opts: ConfigOptions = {}): string {
	const { command, args } = launcher(opts.install);
	return pretty({
		mcpServers: {
			[SERVER_NAME]: {
				command: "cmd",
				args: ["/c", command, ...args],
				env: env(url, opts.key ?? KEY_PLACEHOLDER, opts.corpusId),
			},
		},
	});
}

/**
 * VS Code: `.vscode/mcp.json` with an `inputs` prompt, so the key never sits in the file (VS Code
 * asks for it once: the page shows the key to paste).
 */
export function vscodeJson(url: string, opts: ConfigOptions = {}): string {
	const { command, args } = launcher(opts.install);
	return pretty({
		inputs: [{ type: "promptString", id: "cob-rag-key", description: "cob-rag-engine key", password: true }],
		servers: {
			[SERVER_NAME]: { type: "stdio", command, args, env: env(url, VSCODE_KEY_INPUT, opts.corpusId) },
		},
	});
}

/**
 * n8n and other HTTP clients: install once (the `npm i -g` line only for the npm package), then
 * run the streamable HTTP transport pointed at this engine (`--url` works in every shell, unlike an
 * environment-variable prefix). Each caller sends its own key as a Bearer token.
 */
export function httpCommand(url: string, opts: ConfigOptions = {}): string {
	const start = `cob-rag-mcp start --transport http --host 127.0.0.1 --port ${HTTP_PORT} --url ${url}`;
	return opts.install === "local" ? start : `npm i -g cob-rag-mcp\n${start}`;
}

export interface ClientConfig {
	id: AgentClient;
	/** The main block to copy. */
	code: string;
	language: "shell" | "json";
}

export function configFor(client: AgentClient, url: string, opts: ConfigOptions = {}): ClientConfig {
	switch (client) {
		case "claude-code":
			return { id: client, code: claudeCodeCommand(url, opts), language: "shell" };
		case "claude-desktop":
		case "cursor":
			return { id: client, code: mcpServersJson(url, opts), language: "json" };
		case "vscode":
			return { id: client, code: vscodeJson(url, opts), language: "json" };
		case "n8n":
			return { id: client, code: httpCommand(url, opts), language: "shell" };
	}
}

/** True for addresses only this computer can reach (127.0.0.0/8, localhost, ::1, 0.0.0.0). */
export function isLoopback(url: string): boolean {
	let host: string;
	try {
		host = new URL(url).hostname.toLowerCase();
	} catch {
		return false;
	}
	return (
		host === "localhost" ||
		host.endsWith(".localhost") ||
		host === "[::1]" ||
		host === "0.0.0.0" ||
		/^127(\.\d{1,3}){3}$/.test(host)
	);
}

/** The engine URL agents should use: `/info` public_url when configured, else the dashboard's. */
export function agentEngineUrl(
	info: { public_url?: string | null; public_url_configured?: boolean } | undefined,
	ragUrl: string,
): string {
	const pub = info?.public_url_configured ? info.public_url?.trim() : "";
	return (pub || ragUrl).replace(/\/+$/, "");
}
