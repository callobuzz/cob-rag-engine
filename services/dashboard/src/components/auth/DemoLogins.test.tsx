import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { DemoInfo } from "@/lib/branding/demo";
import { DemoLogins } from "./DemoLogins";

const INFO: DemoInfo = {
	logins: [
		{ role: "operator", email: "op@sahayak.example.com", password: "Nirmal-Clinic-Demo-2026" },
		{ role: "owner", email: "owner@nirmalclinic.example.com", password: "Nirmal-Clinic-Demo-2026" },
	],
	resets_at_utc: "21:30",
};

describe("DemoLogins", () => {
	it("shows both sign-ins, with the password in plain text", () => {
		render(<DemoLogins info={INFO} onUse={() => undefined} />);
		expect(screen.getByRole("heading", { name: "Live demo" })).toBeInTheDocument();
		expect(screen.getByText("Platform owner")).toBeInTheDocument();
		expect(screen.getByText("Business owner")).toBeInTheDocument();
		expect(screen.getByText("op@sahayak.example.com")).toBeInTheDocument();
		expect(screen.getByText("owner@nirmalclinic.example.com")).toBeInTheDocument();
		expect(screen.getAllByText("Nirmal-Clinic-Demo-2026")).toHaveLength(2);
		expect(screen.getByText(/Everything resets every night/)).toBeInTheDocument();
	});

	it("Use hands the chosen sign-in to the form", async () => {
		const onUse = vi.fn();
		render(<DemoLogins info={INFO} onUse={onUse} />);
		await userEvent.click(screen.getByRole("button", { name: "Use the Business owner sign-in" }));
		expect(onUse).toHaveBeenCalledWith(INFO.logins[1]);
		await userEvent.click(screen.getByRole("button", { name: "Use the Platform owner sign-in" }));
		expect(onUse).toHaveBeenLastCalledWith(INFO.logins[0]);
	});

	it("shows the answers left today on the sign-in page, and when they are used up", () => {
		const answers = { limit: 300, left: 41, per_visitor: 15, resets_at: "2026-10-07T21:30:00Z" };
		const { rerender } = render(<DemoLogins info={{ ...INFO, answers }} onUse={() => undefined} />);
		expect(screen.getByTestId("demo-answers")).toHaveTextContent("41 of 300 demo answers left today.");
		rerender(<DemoLogins info={{ ...INFO, answers: { ...answers, left: 0 } }} onUse={() => undefined} />);
		expect(screen.getByTestId("demo-answers")).toHaveTextContent("Today's demo answers are used up.");
	});

	it("shows no numbers when the engine did not send any", () => {
		render(<DemoLogins info={INFO} onUse={() => undefined} />);
		expect(screen.queryByTestId("demo-answers")).toBeNull();
	});
});
