// @vitest-environment jsdom
import fs from "node:fs";
import path from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Markdown } from "./Markdown";
import { Anchors, slugify } from "./slug";

interface Fixture {
	cases: { text: string; id: string }[];
	sequences: { headings: string[]; ids: string[] }[];
}

const ROOT = path.resolve(import.meta.dirname, "../../../../..");
const FIXTURE: Fixture = JSON.parse(
	fs.readFileSync(path.join(ROOT, "tests/fixtures/markdown/heading-ids.json"), "utf8"),
);

afterEach(cleanup);

describe("heading ids shared with the engine", () => {
	it.each(FIXTURE.cases)("slugify($text)", ({ text, id }) => {
		expect(slugify(text)).toBe(id);
	});

	it.each(FIXTURE.sequences)("duplicates in $headings", ({ headings, ids }) => {
		const anchors = new Anchors();
		expect(headings.map((h) => anchors.take(slugify(h)))).toEqual(ids);
	});

	it("renders every case as a heading with that id", () => {
		const text = FIXTURE.cases.map((c) => `## ${c.text}`).join("\n\n");
		const { container } = render(<Markdown text={text} variant="page" />);
		const rendered = [...container.querySelectorAll("h2")].map((h) => h.id);
		const expected = FIXTURE.cases.map((c) => c.id);
		expect(rendered.length).toBe(expected.length);
		// duplicates in the cases would take a counter; the fixture cases are all distinct
		expect(rendered).toEqual(expected);
	});

	it("numbers repeated headings the way the engine does", () => {
		const { container } = render(
			<Markdown text={"## Troubleshooting\n\ntext\n\n## Common tasks\n\n## Troubleshooting"} variant="page" />,
		);
		expect([...container.querySelectorAll("h2")].map((h) => h.id)).toEqual([
			"troubleshooting",
			"common-tasks",
			"troubleshooting-2",
		]);
	});
});

describe("headingIds", () => {
	it("gives level-2 headings the engine anchors in order, then falls back to slugs", () => {
		const { container } = render(
			<Markdown
				text={"## Sign-up modes\n\n### Sub\n\n## Create a customer\n\n## Extra"}
				variant="page"
				headingIds={["signup-modes", "create-customer"]}
			/>,
		);
		expect([...container.querySelectorAll("h2, h3")].map((h) => h.id)).toEqual([
			"signup-modes",
			"sub",
			"create-customer",
			"extra",
		]);
	});
});

describe("page variant", () => {
	it("keeps natural heading levels", () => {
		render(<Markdown text={"# One\n\n## Two\n\n### Three\n\n#### Four"} variant="page" />);
		for (const [level, name] of [
			[1, "One"],
			[2, "Two"],
			[3, "Three"],
			[4, "Four"],
		] as const) {
			expect(screen.getByRole("heading", { level, name })).toBeInTheDocument();
		}
	});

	it("leaves the default variant alone: h3 first, no ids", () => {
		const { container } = render(<Markdown text={"# One"} />);
		expect(container.querySelector("h3")?.id).toBe("");
		expect(container.querySelector("h1")).toBeNull();
	});

	it("keeps links to the app in the app and external ones in a new tab", async () => {
		const onNavigate = vi.fn();
		render(
			<Markdown
				text={"[Widget](/docs/screens/widget) and [site](https://example.test/x) and [up](#top)"}
				variant="page"
				onNavigate={onNavigate}
			/>,
		);
		const internal = screen.getByRole("link", { name: "Widget" });
		expect(internal).toHaveAttribute("href", "/docs/screens/widget");
		expect(internal).not.toHaveAttribute("target");
		await userEvent.click(internal);
		expect(onNavigate).toHaveBeenCalledWith("/docs/screens/widget");
		expect(screen.getByRole("link", { name: "site" })).toHaveAttribute("target", "_blank");
		expect(screen.getByRole("link", { name: "up" })).toHaveAttribute("href", "#top");
	});

	it("still refuses dangerous and protocol-relative targets", () => {
		render(<Markdown text={"[a](javascript:alert(1)) [b](//evil.test/x) [c](/ok)"} variant="page" />);
		expect(screen.queryByRole("link", { name: "a" })).toBeNull();
		expect(screen.queryByRole("link", { name: "b" })).toBeNull();
		expect(screen.getByRole("link", { name: "c" })).toHaveAttribute("href", "/ok");
	});

	it("does not turn [1] into a citation chip", () => {
		const { container } = render(<Markdown text={"See note [1] below"} variant="page" />);
		expect(container.querySelector(".md-cite")).toBeNull();
		expect(container.textContent).toContain("[1]");
	});

	it("puts a copy button on code blocks", () => {
		render(<Markdown text={"```bash\ncurl https://x.test\n```"} variant="page" />);
		expect(screen.getByRole("button", { name: /copy/i })).toBeInTheDocument();
	});
});
