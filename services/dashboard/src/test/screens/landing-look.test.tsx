import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { BASE } from "../msw/data";
import { INFO } from "../msw/handlers/identity";
import { landingState } from "../msw/handlers/landing";
import { server } from "../msw/server";
import { renderApp } from "../render";
import { asAdmin } from "./landing-utils";

/**
 * jsdom's XMLHttpRequest cannot send a FormData holding a File through MSW, so the upload goes as
 * the same fields over fetch (the file as "file:<name>").
 */
vi.mock("@/lib/api/upload", async () => {
	const { errorFromBody, toApiError } = await import("@/lib/api/errors");
	const { currentRagUrl } = await import("@/lib/auth/store");
	return {
		upload: async (opts: { path: string; buildForm: () => FormData; onProgress?: (n: number) => void }) => {
			opts.onProgress?.(0.5);
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

async function openLook() {
	asAdmin();
	const app = renderApp("/admin/landing?tab=look");
	await screen.findByTestId("look-tab");
	return app;
}

const file = (name: string, type: string, size = 100) => {
	const f = new File(["x"], name, { type });
	Object.defineProperty(f, "size", { value: size });
	return f;
};

describe("look: colour and the contrast report", () => {
	it("shows the report, and asks the engine once per burst of changes", async () => {
		await openLook();
		const hex = screen.getByLabelText("Your brand colour");
		expect(screen.getAllByTestId("contrast-row").length).toBeGreaterThan(0);
		fireEvent.change(hex, { target: { value: "#7c3aed" } });
		fireEvent.change(hex, { target: { value: "#7c3aee" } });
		fireEvent.change(hex, { target: { value: "#7c3aef" } });
		await waitFor(() => expect(landingState.themeCalls).toHaveLength(1));
		expect(landingState.themeCalls[0]).toMatchObject({ brand_color: "#7c3aef" });
		// the rows with their ratios and a tick are in the report
		const rows = screen.getAllByTestId("contrast-row");
		expect(within(rows[0] as HTMLElement).getByLabelText("Easy to read")).toBeInTheDocument();
		expect(rows[0]).toHaveTextContent(/\d+\.\d:1/);
	});

	it("says in a sentence where the engine adjusted the colour", async () => {
		await openLook();
		fireEvent.change(screen.getByLabelText("Your brand colour"), { target: { value: "#f5e050" } });
		await waitFor(() =>
			expect(screen.getByTestId("contrast-adjusted")).toHaveTextContent("Buttons use a darker shade of your colour"),
		);
	});

	it("ignores a half-typed code", async () => {
		await openLook();
		fireEvent.change(screen.getByLabelText("Your brand colour"), { target: { value: "#7c3" } });
		await new Promise((r) => setTimeout(r, 400));
		expect(landingState.themeCalls).toHaveLength(0);
	});
});

describe("look: always editable", () => {
	it("keeps colour, second colour and logo editable whatever /info says", async () => {
		server.use(
			http.get(`${BASE}/api/v1/info`, () =>
				HttpResponse.json({
					...INFO,
					features: { ...INFO.features, branding: { white_label: false, remove_branding: false } },
				}),
			),
		);
		await openLook();
		expect(screen.queryByTestId("look-locked")).toBeNull();
		expect(screen.getByLabelText("Your brand colour")).toBeEnabled();
		expect(screen.getByLabelText("Use a second colour for small highlights")).toBeEnabled();
		expect(within(screen.getByTestId("media-logo")).getByLabelText(/Upload a logo/)).toBeEnabled();
	});
});

describe("look: fonts", () => {
	it("is a radio group of six pairings", async () => {
		const app = await openLook();
		const radios = within(screen.getByTestId("font-picker")).getAllByRole("radio");
		expect(radios).toHaveLength(6);
		expect(radios[0]).toBeChecked();
		await app.user.click(screen.getByRole("radio", { name: /Editorial: Calm and editorial/ }));
		expect(screen.getByRole("radio", { name: /Editorial/ })).toBeChecked();
		expect(screen.getByText("Instrument Serif and Inter")).toBeInTheDocument();
	});
});

describe("look: uploads", () => {
	it("lets the keyboard reach each upload control and shows its focus on the visible label", async () => {
		const app = await openLook();
		for (const kind of ["logo", "poster", "video"]) {
			const input = within(screen.getByTestId(`media-${kind}`)).getByLabelText(new RegExp(`Upload a ${kind}`));
			expect(input.className).toContain("peer");
			expect(input.nextElementSibling?.className).toContain("peer-focus-visible:ring-2");
			input.focus();
			expect(input).toHaveFocus();
		}
		await app.user.tab();
	});

	it("checks the type and the size before sending", async () => {
		const app = await openLook();
		const logo = within(screen.getByTestId("media-logo"));
		fireEvent.change(logo.getByLabelText(/Upload a logo/), { target: { files: [file("x.svg", "image/svg+xml")] } });
		expect(await logo.findByRole("alert")).toHaveTextContent("Use a PNG, JPEG or WebP picture.");
		const video = within(screen.getByTestId("media-video"));
		await app.user.upload(video.getByLabelText(/Upload a video/), file("big.mp4", "video/mp4", 30 * 1024 * 1024));
		expect(await video.findByRole("alert")).toHaveTextContent("The most is 25 MB.");
		expect(landingState.assets).toHaveLength(0);
	});

	it("uploads a poster and a video; the video needs the poster first", async () => {
		const app = await openLook();
		const video = within(screen.getByTestId("media-video"));
		await app.user.upload(video.getByLabelText(/Upload a video/), file("demo.mp4", "video/mp4"));
		await waitFor(() => expect(landingState.assets.map((a) => a.kind)).toEqual(["video"]));
		expect(await screen.findByTestId("poster-required")).toBeInTheDocument();
		expect(screen.getByTestId("use-video")).toBeDisabled();
		const poster = within(screen.getByTestId("media-poster"));
		await app.user.upload(poster.getByLabelText(/Upload a poster/), file("poster.png", "image/png"));
		await waitFor(() => expect(screen.getByTestId("use-video")).toBeEnabled());
		expect(screen.queryByTestId("poster-required")).toBeNull();
		await app.user.click(screen.getByTestId("use-video"));
		expect(screen.getByText("You have unsaved changes.")).toBeInTheDocument();
	});

	it("a file the website uses cannot be deleted, and the engine's sentence is shown", async () => {
		const app = await openLook();
		const logo = within(screen.getByTestId("media-logo"));
		await app.user.upload(logo.getByLabelText(/Upload a logo/), file("logo.png", "image/png"));
		await waitFor(() => expect(landingState.assets).toHaveLength(1));
		const id = landingState.assets[0]?.id ?? "";
		landingState.draft.theme = { ...(landingState.draft.theme as object), logo_asset_id: id } as never;
		await app.user.click(await logo.findByRole("button", { name: /Remove the logo/ }));
		expect(await logo.findByRole("alert")).toHaveTextContent("This file is used on your website.");
		expect(landingState.assets).toHaveLength(1);
	});
});
