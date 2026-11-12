import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { normalizeCitations } from "./cite";
import { Markdown } from "./Markdown";
import { safeUrl } from "./url";

describe("<Markdown>", () => {
	it("renders a table in a keyboard-scrollable region", () => {
		render(<Markdown text={"| Plan | Price |\n|---|--:|\n| Basic | ₹500 |"} />);
		const region = screen.getByRole("region", { name: "Table" });
		expect(region).toHaveAttribute("tabindex", "0");
		expect(region.querySelectorAll("th")).toHaveLength(2);
		expect(screen.getByText("₹500")).toHaveClass("md-right");
	});

	it("turns a citation into a button that reports its number, only for known sources", () => {
		const onCite = vi.fn();
		render(<Markdown text="Open daily [1] and sometimes [4]." sources={[1, 2]} onCite={onCite} />);
		fireEvent.click(screen.getByRole("button", { name: "1" }));
		expect(onCite).toHaveBeenCalledWith(1);
		expect(screen.queryByRole("button", { name: "4" })).toBeNull();
		expect(screen.getByText(/sometimes \[4\]\./)).toBeInTheDocument();
	});

	it("never creates elements from model HTML or unsafe links", () => {
		const { container } = render(
			<Markdown
				text={
					'<img src=x onerror="window.__x=1"> <script>window.__x=1</script> [a](javascript:alert(1)) [b](https://example.com)'
				}
			/>,
		);
		expect(container.querySelector("img, script")).toBeNull();
		const links = [...container.querySelectorAll("a")];
		expect(links.map((a) => a.getAttribute("href"))).toEqual(["https://example.com"]);
		expect(links[0]).toHaveAttribute("rel", "noopener noreferrer nofollow");
		expect(links[0]).toHaveAttribute("target", "_blank");
		expect(container.textContent).toContain("<script>window.__x=1</script>");
	});

	it("copies a code block", async () => {
		const writeText = vi.fn().mockResolvedValue(undefined);
		Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
		render(<Markdown text={"```bash\nmake up\n```"} />);
		fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
		expect(writeText).toHaveBeenCalledWith("make up");
		await waitFor(() => expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument());
	});

	it("keeps finished blocks mounted while the answer grows", () => {
		const { container, rerender } = render(<Markdown text={"First **block**\n\nSecond line\n"} streaming />);
		const first = container.querySelector("p");
		rerender(<Markdown text={"First **block**\n\nSecond paragraph, longer\n\nThird"} streaming />);
		expect(container.querySelector("p")).toBe(first);
		rerender(<Markdown text={"First **block**\n\nSecond paragraph, longer\n\nThird one"} />);
		expect(container.querySelector("p")).toBe(first);
		expect(container.querySelectorAll("p")).toHaveLength(3);
	});

	it("marks a streaming answer so the caret can ride on its last block", () => {
		const { container, rerender } = render(<Markdown text="Hello" streaming />);
		expect(container.firstElementChild).toHaveClass("md-streaming");
		rerender(<Markdown text="Hello" />);
		expect(container.firstElementChild).not.toHaveClass("md-streaming");
	});

	it("inline mode has no paragraph wrapper", () => {
		const { container } = render(<Markdown inline text="Call us on [+91 98765 43210](tel:+919876543210)" />);
		expect(container.querySelector("p")).toBeNull();
		expect(container.querySelector("a")).toHaveAttribute("href", "tel:+919876543210");
	});
});

describe("helpers", () => {
	it("safeUrl allows only http, https, mailto and tel", () => {
		// The target is returned as written: entities are decoded only to check the scheme.
		for (const same of [
			"https://example.com/a?b=1&amp;c=2",
			"https://x.com/p?id=1&tab=pricing",
			"https://x.com/p?price&lt=500",
		]) {
			expect(safeUrl(same)).toBe(same);
		}
		for (const bad of [
			"javascript:alert(1)",
			"JaVaScRiPt:1",
			"data:text/html,x",
			"&#x6A;avascript:1",
			"//a.b",
			"/x",
			"",
		]) {
			expect(safeUrl(bad)).toBeNull();
		}
	});

	it("normalizeCitations rewrites the other marker forms to [n]", () => {
		expect(normalizeCitations("a【1】 b[^2] c^3 d")).toBe("a[1] b[2] c[3] d");
		expect(normalizeCitations("2^10 is big")).toBe("2^10 is big");
	});
});
