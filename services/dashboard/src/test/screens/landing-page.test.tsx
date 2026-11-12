import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import {
	useConnectAsk,
	useDeleteAsset,
	useDisconnectAsk,
	useLanding,
	useLandingSettings,
	usePreviewLink,
	usePublish,
	useRestore,
	useSaveDraft,
	useThemePreview,
	useUploadAsset,
	useVersions,
} from "@/features/landing/api";
import { principalFromSession } from "@/lib/auth/principal";
import { RT_KEY } from "@/lib/auth/storage";
import { pageLabel, type View, visiblePages } from "@/lib/nav";
import { landingStrings } from "@/lib/strings/landing";
import { rt } from "../msw/data";
import { landingState, seedPublished, stateOut } from "../msw/handlers/landing";
import { renderApp } from "../render";
import { asAdmin } from "./landing-utils";

function wrapper({ children }: { children: ReactNode }) {
	const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
	return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

/**
 * jsdom's XMLHttpRequest cannot send a FormData holding a File through MSW, so the upload goes as
 * the same fields over fetch (the file as "file:<name>").
 */
vi.mock("@/lib/api/upload", async () => {
	const { errorFromBody, toApiError } = await import("@/lib/api/errors");
	const { currentRagUrl } = await import("@/lib/auth/store");
	return {
		upload: async (opts: { path: string; buildForm: () => FormData }) => {
			const params = new URLSearchParams();
			for (const [k, v] of opts.buildForm().entries()) params.append(k, typeof v === "string" ? v : `file:${v.name}`);
			let res: Response;
			try {
				res = await fetch(`${currentRagUrl()}${opts.path}`, { method: "POST", body: params });
			} catch (e) {
				throw toApiError(e);
			}
			const text = await res.text();
			const body: unknown = text ? JSON.parse(text) : null;
			if (res.ok) return body;
			throw errorFromBody(res.status, body, res.headers);
		},
	};
});

const OWNER = principalFromSession({
	user: { id: "u1", email: "priya@shree-labs.in", name: "Priya Kulkarni", is_superadmin: false },
	tenant: { id: "t1", name: "Shree Dental Clinic", role: "owner", status: "active", is_operator: false },
	tenants: [],
});
const ADMIN = principalFromSession({
	user: { id: "u0", email: "sv@sahayak.example", name: "SV", is_superadmin: true },
	tenant: null,
	tenants: [],
});

describe("/admin/landing: who sees it", () => {
	it("the menu lists Public website for the operator only", () => {
		const labels = (p: typeof ADMIN, view: View) =>
			visiblePages(p, { content_portal: false }, undefined).map((page) => pageLabel(page, view));
		expect(labels(ADMIN, "admin")).toContain("Public website");
		expect(labels(OWNER, "tenant")).not.toContain("Public website");
	});

	it("a workspace view gets the not-available screen", async () => {
		sessionStorage.setItem(RT_KEY, rt(1));
		renderApp("/admin/landing");
		await waitFor(() => expect(screen.queryByRole("heading", { level: 1, name: "Public website" })).toBeNull());
	});

	it("the operator sees the page frame with the switch off and the reason", async () => {
		asAdmin();
		renderApp("/admin/landing");
		expect(await screen.findByRole("heading", { level: 1, name: "Public website" })).toBeInTheDocument();
		expect(await screen.findByTestId("landing-on-state")).toHaveTextContent("Website is off");
		expect(screen.getByRole("switch", { name: landingStrings.status.switchLabel })).toBeDisabled();
		expect(screen.getByText(landingStrings.status.needPublish)).toBeInTheDocument();
		for (const label of Object.values(landingStrings.tabs)) {
			expect(screen.getByRole("tab", { name: label })).toBeInTheDocument();
		}
	});

	it("the switch works once something is published and an address is set", async () => {
		asAdmin();
		seedPublished();
		landingState.address = "https://www.sharmadental.in";
		const app = renderApp("/admin/landing");
		const toggle = await screen.findByRole("switch", { name: landingStrings.status.switchLabel });
		await waitFor(() => expect(toggle).toBeEnabled());
		await app.user.click(toggle);
		await waitFor(() => expect(screen.getByTestId("landing-on-state")).toHaveTextContent("Website is on"));
		expect(landingState.enabled).toBe(true);
		expect(screen.getByRole("link", { name: /View website/ })).toHaveAttribute("href", "https://www.sharmadental.in");
	});
});

describe("the data hooks call the pinned routes", () => {
	it("reads the state and the versions", async () => {
		seedPublished("First version");
		const state = renderHook(() => useLanding(), { wrapper });
		await waitFor(() => expect(state.result.current.data?.draft_revision).toBe(1));
		const versions = renderHook(() => useVersions(), { wrapper });
		await waitFor(() => expect(versions.result.current.data?.[0]?.change_note).toBe("First version"));
	});

	it("saves with the loaded revision, publishes, restores and switches", async () => {
		const save = renderHook(() => useSaveDraft(), { wrapper });
		const draft = stateOut().draft;
		await save.result.current.mutateAsync({ base_revision: 1, content: draft });
		expect(landingState.revision).toBe(2);
		await expect(save.result.current.mutateAsync({ base_revision: 1, content: draft })).rejects.toMatchObject({
			code: "landing_revision_conflict",
		});

		const publish = renderHook(() => usePublish(), { wrapper });
		await publish.result.current.mutateAsync({ base_revision: 2, change_note: "Hello" });
		expect(landingState.versions).toHaveLength(1);

		const restore = renderHook(() => useRestore(), { wrapper });
		await restore.result.current.mutateAsync({ version: 1, base_revision: 2 });
		expect(landingState.revision).toBe(3);

		const settings = renderHook(() => useLandingSettings(), { wrapper });
		await settings.result.current.mutateAsync({ address: "https://sharmadental.in" });
		await settings.result.current.mutateAsync({ enabled: true });
		expect(landingState.enabled).toBe(true);
	});

	it("previews the theme, makes a preview link, uploads, deletes, connects and disconnects", async () => {
		const theme = renderHook(() => useThemePreview(), { wrapper });
		const out = await theme.result.current.mutateAsync({ brand_color: "#7c3aed", base: "light" });
		expect(out.light.brand).toBe("#7c3aed");

		const link = renderHook(() => usePreviewLink(), { wrapper });
		const made = await link.result.current.mutateAsync({ page: "home", lang: "en" });
		expect(made.url).toContain("/_landing/preview/home");

		const up = renderHook(() => useUploadAsset(), { wrapper });
		const asset = await up.result.current.mutateAsync({
			kind: "poster",
			file: new File(["x"], "poster.png", { type: "image/png" }),
		});
		expect(landingState.assets.map((a) => a.id)).toContain(asset.id);
		const del = renderHook(() => useDeleteAsset(), { wrapper });
		await del.result.current.mutateAsync(asset.id);
		expect(landingState.assets).toHaveLength(0);

		landingState.address = "https://sharmadental.in";
		const connect = renderHook(() => useConnectAsk(), { wrapper });
		const connected = await connect.result.current.mutateAsync("11111111-1111-4111-8111-111111111111");
		expect(connected.ask.status).toBe("ready");
		const disconnect = renderHook(() => useDisconnectAsk(), { wrapper });
		expect((await disconnect.result.current.mutateAsync()).ask.status).toBe("not_connected");
	});
});

describe("the words", () => {
	it("never say corpus, tenant or LiteLLM", () => {
		const text = JSON.stringify(landingStrings);
		expect(text).not.toMatch(/corpus|tenant|litellm|minor unit/i);
	});
});
