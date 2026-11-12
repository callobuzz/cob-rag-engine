import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CtaButton } from "./CtaButton";
import { QuickReplyChips } from "./QuickReplyChips";
import { ReplyExtras } from "./ReplyExtras";

describe("QuickReplyChips", () => {
	it("renders up to four chips and sends the message", async () => {
		const onPick = vi.fn();
		render(
			<QuickReplyChips
				items={[
					{ label: "Fasting rules", message: "Which tests need fasting?" },
					{ label: "Report time", message: "When is my report ready?" },
					{ label: "Home pickup", message: "Is home pickup free?" },
					{ label: "Prices", message: "What does CBC cost?" },
					{ label: "Extra", message: "x" },
				]}
				onPick={onPick}
			/>,
		);
		expect(screen.getAllByRole("button")).toHaveLength(4);
		await userEvent.click(screen.getByRole("button", { name: "Report time" }));
		expect(onPick).toHaveBeenCalledWith("When is my report ready?");
		expect(screen.getByRole("group", { name: "Suggestions" })).toBeInTheDocument();
	});

	it("renders nothing without items", () => {
		const { container } = render(<QuickReplyChips items={[]} onPick={() => {}} />);
		expect(container).toBeEmptyDOMElement();
	});

	it("disables the chips while another answer streams", () => {
		render(<QuickReplyChips items={[{ label: "A", message: "a?" }]} onPick={() => {}} disabled />);
		expect(screen.getByRole("button", { name: "A" })).toBeDisabled();
	});
});

describe("CtaButton", () => {
	it("opens a safe link in a new tab and can be dismissed", async () => {
		const onDismiss = vi.fn();
		render(
			<CtaButton
				cta={{ type: "whatsapp", label: "Chat on WhatsApp", url: "https://wa.me/919812345678?text=CBC" }}
				onDismiss={onDismiss}
			/>,
		);
		const link = screen.getByRole("link", { name: /Chat on WhatsApp/ });
		expect(link).toHaveAttribute("href", "https://wa.me/919812345678?text=CBC");
		expect(link).toHaveAttribute("target", "_blank");
		expect(link).toHaveAttribute("rel", "noopener noreferrer");
		await userEvent.click(screen.getByRole("button", { name: "Not now" }));
		expect(onDismiss).toHaveBeenCalled();
	});

	it("never renders an unsafe URL", () => {
		const { container } = render(
			<CtaButton cta={{ type: "link", label: "x", url: "javascript:alert(1)" }} onDismiss={() => {}} />,
		);
		expect(container).toBeEmptyDOMElement();
	});

	it("shows the form kinds as a note where nothing handles them", () => {
		render(<CtaButton cta={{ type: "handoff", label: "Talk to a person", url: null }} onDismiss={() => {}} />);
		expect(screen.queryByRole("button", { name: "Talk to a person" })).toBeNull();
		expect(screen.getByText("Talk to a person")).toBeInTheDocument();
		expect(screen.getByText(/opens the “talk to a person” card/)).toBeInTheDocument();
	});

	it("a form kind is a button when something handles it", async () => {
		const onAction = vi.fn();
		render(
			<CtaButton
				cta={{ type: "lead_form", label: "Leave your number", url: null }}
				onDismiss={() => {}}
				onAction={onAction}
			/>,
		);
		await userEvent.click(screen.getByRole("button", { name: "Leave your number" }));
		expect(onAction).toHaveBeenCalledWith("lead_form");
	});
});

describe("ReplyExtras", () => {
	const cta = { type: "call", label: "Call us", url: "tel:+919812345678" };
	const replies = [{ label: "Fasting rules", message: "Which tests need fasting?" }];

	it("shows chips only on the newest answer and the button on any", () => {
		const { rerender } = render(
			<ReplyExtras replies={replies} cta={cta} latest={false} disabled={false} onSend={() => {}} />,
		);
		expect(screen.queryByRole("group", { name: "Suggestions" })).toBeNull();
		expect(screen.getByRole("link", { name: /Call us/ })).toBeInTheDocument();
		rerender(<ReplyExtras replies={replies} cta={null} latest disabled={false} onSend={() => {}} />);
		expect(screen.getByRole("button", { name: "Fasting rules" })).toBeInTheDocument();
	});

	it("'Not now' hides the button for this answer", async () => {
		render(<ReplyExtras replies={[]} cta={cta} latest disabled={false} onSend={() => {}} />);
		await userEvent.click(screen.getByRole("button", { name: "Not now" }));
		expect(screen.queryByRole("link", { name: /Call us/ })).toBeNull();
	});

	it("renders nothing when there is nothing to offer", () => {
		const { container } = render(<ReplyExtras replies={[]} cta={null} latest disabled={false} onSend={() => {}} />);
		expect(container).toBeEmptyDOMElement();
	});
});
