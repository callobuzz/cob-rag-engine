import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { KeyPermission } from "./api";
import { PermissionBadge, PermissionPicker, permissionRank } from "./PermissionPicker";

function Harness({ start = "read" }: { start?: KeyPermission }) {
	const [value, setValue] = useState<KeyPermission>(start);
	return <PermissionPicker value={value} onChange={setValue} />;
}

describe("permission picker", () => {
	it("is a radio group of read, write and full, each saying what it allows", () => {
		render(<Harness />);
		const group = screen.getByRole("group", { name: "Permission" });
		expect(group).toBeInTheDocument();
		const radios = screen.getAllByRole("radio");
		expect(radios.map((r) => r.getAttribute("value"))).toEqual(["read", "write", "full"]);
		expect(radios.map((r) => (r as HTMLInputElement).checked)).toEqual([true, false, false]);
		expect(screen.getByText("Search, ask, list and get. Cannot change anything.")).toBeInTheDocument();
		expect(screen.getByText(/Everything, including deleting and managing keys/)).toBeInTheDocument();
	});

	it("moves the choice with a click and with the arrow keys", async () => {
		const user = userEvent.setup();
		render(<Harness />);
		const [read, write, full] = screen.getAllByRole("radio") as HTMLInputElement[];
		await user.click(write as HTMLInputElement);
		expect(write?.checked).toBe(true);
		await user.keyboard("{ArrowDown}");
		expect(full?.checked).toBe(true);
		expect(read?.checked).toBe(false);
	});

	it("ranks read below write below full, and shows a badge for each", () => {
		expect(permissionRank("read")).toBeLessThan(permissionRank("write"));
		expect(permissionRank("write")).toBeLessThan(permissionRank("full"));
		render(
			<>
				<PermissionBadge permission="read" />
				<PermissionBadge permission="write" />
				<PermissionBadge permission="full" />
			</>,
		);
		expect(screen.getByText("Read")).toHaveAttribute("data-variant", "ok");
		expect(screen.getByText("Write")).toHaveAttribute("data-variant", "warn");
		expect(screen.getByText("Full")).toHaveAttribute("data-variant", "bad");
	});
});

describe("permission picker names", () => {
	it("can be found by role and name", () => {
		render(<Harness />);
		expect(screen.getByRole("radio", { name: /^Read/ })).toBeChecked();
		expect(screen.getByRole("radio", { name: /^Write/ })).not.toBeChecked();
		expect(screen.getByRole("radio", { name: /^Full/ })).not.toBeChecked();
	});
});
