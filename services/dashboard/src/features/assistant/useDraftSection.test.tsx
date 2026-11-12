import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { BASE, CORPORA } from "@/test/msw/data";
import { assistantStateFor, DRAFT_CONFIG } from "@/test/msw/handlers/assistant";
import { server } from "@/test/msw/server";
import { parseConfig } from "./api";
import { identitySchema, voiceSchema } from "./schemas";
import type { AssistantState } from "./types";
import { useDraftSectionCore } from "./useDraftSection";

const KB = CORPORA[0]?.id ?? "";
const wrap = ({ children }: { children: ReactNode }) => (
	<QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
);

function state(over: { liveEnabled?: boolean; draftEnabled?: boolean } = {}): AssistantState {
	const s = assistantStateFor(KB);
	const cfg = (enabled: boolean) => ({ ...parseConfig(DRAFT_CONFIG), enabled });
	return {
		corpus_id: KB,
		enabled: over.liveEnabled ?? true,
		schema_version: 1,
		published: s.published ? { ...s.published, config: cfg(over.liveEnabled ?? true) } : null,
		draft: s.draft ? { ...s.draft, config: cfg(over.draftEnabled ?? true) } : null,
	} as AssistantState;
}

describe("useDraftSectionCore", () => {
	it("sends the whole config with only its section changed, at the draft revision", async () => {
		const seen: { body?: { base_revision: number; config: Record<string, unknown> } } = {};
		server.use(
			http.put(`${BASE}/api/v1/corpora/${KB}/assistant/draft`, async ({ request }) => {
				seen.body = (await request.json()) as never;
				return HttpResponse.json({ ...assistantStateFor(KB).draft, draft_revision: 4 });
			}),
		);
		const { result } = renderHook(() => useDraftSectionCore(KB, state(), "identity", identitySchema), {
			wrapper: wrap,
		});
		act(() => result.current.form.setValue("assistant_name", "Meera", { shouldDirty: true }));
		await act(() => result.current.submit.onSubmit());
		expect(seen.body?.base_revision).toBe(3);
		const identity = seen.body?.config.identity as { assistant_name: string } | undefined;
		expect(identity?.assistant_name).toBe("Meera");
		expect(seen.body?.config.voice).toEqual(parseConfig(DRAFT_CONFIG).voice);
	});

	it("a draft saved while the assistant was on never revives a switched-off one", async () => {
		const seen: { body?: { config: { enabled: boolean } } } = {};
		server.use(
			http.put(`${BASE}/api/v1/corpora/${KB}/assistant/draft`, async ({ request }) => {
				seen.body = (await request.json()) as never;
				return HttpResponse.json({ ...assistantStateFor(KB).draft, draft_revision: 4 });
			}),
		);
		const { result } = renderHook(
			() => useDraftSectionCore(KB, state({ liveEnabled: false, draftEnabled: true }), "voice", voiceSchema),
			{ wrapper: wrap },
		);
		act(() => result.current.form.setValue("custom_tone", "calm", { shouldDirty: true }));
		await act(() => result.current.submit.onSubmit());
		expect(seen.body?.config.enabled).toBe(false);
	});

	it("a conflict is exposed, not shown as a field error", async () => {
		server.use(
			http.put(`${BASE}/api/v1/corpora/${KB}/assistant/draft`, () =>
				HttpResponse.json({ detail: "x", code: "assistant_draft_conflict", current_revision: 9 }, { status: 409 }),
			),
		);
		const { result } = renderHook(() => useDraftSectionCore(KB, state(), "identity", identitySchema), {
			wrapper: wrap,
		});
		act(() => result.current.form.setValue("assistant_name", "Meera", { shouldDirty: true }));
		await act(() => result.current.submit.onSubmit());
		await waitFor(() => expect(result.current.conflict?.extra.current_revision).toBe(9));
		expect(result.current.form.formState.errors.assistant_name).toBeUndefined();
	});

	it("the engine's 422 path lands on the field in this section", async () => {
		server.use(
			http.put(`${BASE}/api/v1/corpora/${KB}/assistant/draft`, () =>
				HttpResponse.json(
					{
						detail: [
							{
								loc: ["body", "config", "identity", "card", "phone"],
								msg: "not a valid Indian phone number",
								type: "x",
							},
						],
					},
					{ status: 422 },
				),
			),
		);
		const { result } = renderHook(() => useDraftSectionCore(KB, state(), "identity", identitySchema), {
			wrapper: wrap,
		});
		act(() => result.current.form.setValue("assistant_name", "Meera", { shouldDirty: true }));
		await act(() => result.current.submit.onSubmit());
		await waitFor(() =>
			expect(result.current.form.formState.errors.card?.phone?.message).toBe("not a valid Indian phone number"),
		);
	});
});
