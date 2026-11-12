import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RT_KEY } from "@/lib/auth/storage";
import { IndexPage } from "./IndexPage";

const principal = vi.hoisted(() => ({ current: null as { view: string } | null }));

vi.mock("@/lib/auth/store", () => ({ usePrincipal: () => principal.current }));
vi.mock("@/features/home/HomePage", () => ({ HomePage: () => <p>A business Home</p> }));
vi.mock("@/features/overview/OverviewPage", () => ({ OverviewPage: () => <p>The engine Overview</p> }));

describe("IndexPage (the dashboard's home at / and /app)", () => {
	beforeEach(() => {
		principal.current = null;
	});

	it("shows Home to a business", () => {
		principal.current = { view: "tenant" };
		render(<IndexPage />);
		expect(screen.getByText("A business Home")).toBeInTheDocument();
	});

	it("shows the Overview to the operator", () => {
		principal.current = { view: "admin" };
		render(<IndexPage />);
		expect(screen.getByText("The engine Overview")).toBeInTheDocument();
	});
});

describe("the public website's signed-in redirect", () => {
	it("still reads the key the dashboard writes", () => {
		// landing/render.py REDIRECT_SCRIPT looks for exactly this name; renaming it breaks the redirect.
		expect(RT_KEY).toBe("cob.rt");
	});
});
