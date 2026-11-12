import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { VisitorLinks, visitorOf, WebsiteBadge, whatsappLink } from "@/features/support-admin/AdminSupportPage";

// LP3 landing: a request written on the public website's contact page.
describe("website enquiries in the support inbox", () => {
	it("tells a visitor's request from a workspace's by its context", () => {
		expect(visitorOf({ plan: "Pro" })).toBeNull();
		expect(visitorOf({ landing_contact: { name: "Priya Sharma", email: "priya@sharmadental.in" } })).toMatchObject({
			name: "Priya Sharma",
		});
	});

	it("builds a WhatsApp link from an Indian number, or none from nonsense", () => {
		expect(whatsappLink("98220 12345")).toBe("https://wa.me/919822012345");
		expect(whatsappLink("+91 98220 12345")).toBe("https://wa.me/919822012345");
		expect(whatsappLink("12")).toBeNull();
	});

	it("shows the visitor's name and the ways to write back", () => {
		render(<VisitorLinks visitor={{ name: "Priya Sharma", email: "priya@sharmadental.in", phone: "98220 12345" }} />);
		const links = screen.getByTestId("visitor-links");
		expect(links).toHaveTextContent("Priya Sharma");
		expect(screen.getByRole("link", { name: /priya@sharmadental.in/ })).toHaveAttribute(
			"href",
			"mailto:priya@sharmadental.in",
		);
		expect(screen.getByRole("link", { name: /98220 12345/ })).toHaveAttribute("href", "tel:9822012345");
		expect(screen.getByRole("link", { name: "WhatsApp" })).toHaveAttribute("href", "https://wa.me/919822012345");
	});

	it("leaves out a way that was not given", () => {
		render(<VisitorLinks visitor={{ name: "Asha", email: "asha@x.in", phone: null }} />);
		expect(screen.queryByRole("link", { name: "WhatsApp" })).toBeNull();
		expect(screen.queryByRole("link", { name: /Phone/ })).toBeNull();
	});

	it("marks the row as coming from the website", () => {
		render(<WebsiteBadge />);
		expect(screen.getByTestId("website-badge")).toHaveTextContent("From your website");
	});
});
