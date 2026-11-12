import { describe, expect, it } from "vitest";
import {
	agentEngineUrl,
	CONFIG_FILES,
	claudeCodeCommand,
	configFor,
	DEFAULT_INSTALL,
	httpCommand,
	isLoopback,
	KEY_PLACEHOLDER,
	LOCAL_INSTALL_STEPS,
	launcher,
	MCP_PUBLISHED,
	mcpServersJson,
	mcpServersJsonWindowsCmd,
	VSCODE_KEY_INPUT,
	vscodeJson,
} from "./configs";

const URL_ = "https://rag.sahayak.ai";

describe("cob-rag-mcp client configs", () => {
	it("Claude Code: the README command with this engine's URL and the key placeholder", () => {
		expect(claudeCodeCommand(URL_)).toBe(
			"claude mcp add cob-rag -e COB_RAG_URL=https://rag.sahayak.ai -e COB_RAG_API_KEY=ck_your_knowledge_base_key -- npx -y cob-rag-mcp start",
		);
	});

	it("Claude Desktop / Cursor: valid mcpServers JSON", () => {
		expect(JSON.parse(mcpServersJson(URL_))).toEqual({
			mcpServers: {
				"cob-rag": {
					command: "npx",
					args: ["-y", "cob-rag-mcp", "start"],
					env: { COB_RAG_URL: URL_, COB_RAG_API_KEY: KEY_PLACEHOLDER },
				},
			},
		});
		expect(configFor("cursor", URL_).code).toBe(configFor("claude-desktop", URL_).code);
	});

	it("Windows: cmd /c npx variant is valid JSON", () => {
		const cfg = JSON.parse(mcpServersJsonWindowsCmd(URL_));
		expect(cfg.mcpServers["cob-rag"]).toMatchObject({
			command: "cmd",
			args: ["/c", "npx", "-y", "cob-rag-mcp", "start"],
		});
	});

	it("VS Code: inputs prompt for the key, stdio server", () => {
		expect(VSCODE_KEY_INPUT).toBe(["$", "{input:cob-rag-key}"].join(""));
		const cfg = JSON.parse(vscodeJson(URL_));
		expect(cfg.inputs).toEqual([
			{ type: "promptString", id: "cob-rag-key", description: "cob-rag-engine key", password: true },
		]);
		expect(cfg.servers["cob-rag"]).toEqual({
			type: "stdio",
			command: "npx",
			args: ["-y", "cob-rag-mcp", "start"],
			env: { COB_RAG_URL: URL_, COB_RAG_API_KEY: VSCODE_KEY_INPUT },
		});
	});

	it("values that need escaping stay valid JSON (a Windows-style path or quotes)", () => {
		const odd = 'http://192.168.1.20:8000/a"b\\c';
		expect(JSON.parse(mcpServersJson(odd)).mcpServers["cob-rag"].env.COB_RAG_URL).toBe(odd);
		expect(JSON.parse(vscodeJson(odd)).servers["cob-rag"].env.COB_RAG_URL).toBe(odd);
	});

	it("config file locations quote Windows paths with single backslashes", () => {
		expect(CONFIG_FILES.claudeDesktop.win).toBe("%APPDATA%\\Claude\\claude_desktop_config.json");
		expect(CONFIG_FILES.claudeDesktop.win.split("\\")).toEqual(["%APPDATA%", "Claude", "claude_desktop_config.json"]);
		expect(CONFIG_FILES.claudeDesktop.mac).toBe("~/Library/Application Support/Claude/claude_desktop_config.json");
	});

	it("n8n / HTTP: the streamable HTTP transport on 127.0.0.1:3333", () => {
		expect(httpCommand(URL_)).toBe(
			"npm i -g cob-rag-mcp\ncob-rag-mcp start --transport http --host 127.0.0.1 --port 3333 --url https://rag.sahayak.ai",
		);
	});
});

describe("local install (before cob-rag-mcp is on npm)", () => {
	it("runs the cob-rag-mcp command from npm link, with the same environment variables", () => {
		expect(launcher("local")).toEqual({ command: "cob-rag-mcp", args: ["start"] });
		expect(launcher("npx")).toEqual({ command: "npx", args: ["-y", "cob-rag-mcp", "start"] });
		expect(claudeCodeCommand(URL_, { install: "local" })).toBe(
			"claude mcp add cob-rag -e COB_RAG_URL=https://rag.sahayak.ai -e COB_RAG_API_KEY=ck_your_knowledge_base_key -- cob-rag-mcp start",
		);
		expect(JSON.parse(mcpServersJson(URL_, { install: "local" })).mcpServers["cob-rag"]).toEqual({
			command: "cob-rag-mcp",
			args: ["start"],
			env: { COB_RAG_URL: URL_, COB_RAG_API_KEY: KEY_PLACEHOLDER },
		});
		expect(JSON.parse(mcpServersJsonWindowsCmd(URL_, { install: "local" })).mcpServers["cob-rag"]).toMatchObject({
			command: "cmd",
			args: ["/c", "cob-rag-mcp", "start"],
		});
		expect(httpCommand(URL_, { install: "local" })).toBe(
			"cob-rag-mcp start --transport http --host 127.0.0.1 --port 3333 --url https://rag.sahayak.ai",
		);
	});

	it("the install steps are the README's", () => {
		expect(LOCAL_INSTALL_STEPS).toEqual([
			"git clone https://github.com/callobuzz/cob-rag-mcp.git",
			"cd cob-rag-mcp",
			"pnpm install && pnpm build && npm link",
		]);
	});

	it("defaults to the local install until the package is published", () => {
		expect(DEFAULT_INSTALL).toBe(MCP_PUBLISHED ? "npx" : "local");
	});
});

describe("a real key in the configs", () => {
	const key = "ck_7b221003_new_1";

	it("replaces the placeholder in every client, and a customer key names its default knowledge base", () => {
		expect(claudeCodeCommand(URL_, { key })).toContain(`COB_RAG_API_KEY=${key} --`);
		expect(claudeCodeCommand(URL_, { key })).not.toContain(KEY_PLACEHOLDER);
		expect(JSON.parse(mcpServersJson(URL_, { key })).mcpServers["cob-rag"].env.COB_RAG_API_KEY).toBe(key);
		expect(claudeCodeCommand(URL_, { key: "tk_1_x", corpusId: "kb-1" })).toContain(
			"-e COB_RAG_API_KEY=tk_1_x -e COB_RAG_CORPUS_ID=kb-1 --",
		);
		expect(JSON.parse(mcpServersJson(URL_, { key: "tk_1_x", corpusId: "kb-1" })).mcpServers["cob-rag"].env).toEqual({
			COB_RAG_URL: URL_,
			COB_RAG_API_KEY: "tk_1_x",
			COB_RAG_CORPUS_ID: "kb-1",
		});
	});

	it("VS Code never writes the key into the file", () => {
		const cfg = JSON.parse(vscodeJson(URL_, { key, corpusId: "kb-1" }));
		expect(cfg.servers["cob-rag"].env).toEqual({
			COB_RAG_URL: URL_,
			COB_RAG_API_KEY: VSCODE_KEY_INPUT,
			COB_RAG_CORPUS_ID: "kb-1",
		});
		expect(vscodeJson(URL_, { key })).not.toContain(key);
	});
});

describe("engine URL for agents", () => {
	it("uses /info public_url only when configured", () => {
		expect(agentEngineUrl({ public_url: "https://rag.sahayak.ai/", public_url_configured: true }, "http://x")).toBe(
			"https://rag.sahayak.ai",
		);
		expect(
			agentEngineUrl({ public_url: "http://127.0.0.1:8000", public_url_configured: false }, "http://10.0.0.5:8000"),
		).toBe("http://10.0.0.5:8000");
		expect(agentEngineUrl(undefined, "http://127.0.0.1:8000/")).toBe("http://127.0.0.1:8000");
	});

	it("detects loopback addresses", () => {
		for (const u of ["http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000", "http://127.1.2.3"]) {
			expect(isLoopback(u), u).toBe(true);
		}
		for (const u of ["https://rag.sahayak.ai", "http://192.168.1.20:8000", "not a url"]) {
			expect(isLoopback(u), u).toBe(false);
		}
	});
});
