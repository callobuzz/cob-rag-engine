/**
 * Razorpay's checkout window (P1). The script is loaded here and nowhere else, and only on the
 * `/pay` page: the dashboard's nginx gives that one page a CSP that allows `checkout.razorpay.com`.
 * A page's CSP is fixed when its document loads, so every way into the pay page is a FULL page load
 * (`window.location.assign`), never the client-side router.
 */
export const CHECKOUT_SRC = "https://checkout.razorpay.com/v1/checkout.js";

export interface PayParams {
	purpose: "top_up" | "plan" | "add_on";
	amount?: number;
	addOn?: string;
}

/** The address of the pay page for these parameters. */
export function payUrl(p: PayParams): string {
	const q = new URLSearchParams({ purpose: p.purpose });
	if (p.amount != null) q.set("amount", String(p.amount));
	if (p.addOn) q.set("add_on", p.addOn);
	return `/pay?${q.toString()}`;
}

/** The one place that leaves the SPA: a full page load. Tests replace `go`. */
export const pageLoad = {
	go(url: string): void {
		window.location.assign(url);
	},
};

/** Go to the pay page with a full page load (see the file comment for why). */
export function goToPay(p: PayParams): void {
	pageLoad.go(payUrl(p));
}

export interface RazorpayResponse {
	razorpay_payment_id: string;
	razorpay_order_id: string;
	razorpay_signature: string;
}

export interface RazorpayOptions {
	key: string;
	order_id: string;
	amount: number;
	currency: string;
	name: string;
	description: string;
	prefill?: Record<string, string>;
	notes?: Record<string, string>;
	theme?: { color?: string };
	handler: (response: RazorpayResponse) => void;
	modal?: { ondismiss?: () => void };
}

interface RazorpayInstance {
	open(): void;
	on(event: "payment.failed", cb: (response: { error?: { description?: string } }) => void): void;
}

declare global {
	interface Window {
		Razorpay?: new (options: RazorpayOptions) => RazorpayInstance;
	}
}

let loading: Promise<void> | null = null;

/** Add the checkout script once. Resolves when `window.Razorpay` exists; rejects when it cannot load. */
export function loadCheckout(): Promise<void> {
	if (typeof window.Razorpay === "function") return Promise.resolve();
	if (loading) return loading;
	loading = new Promise<void>((resolve, reject) => {
		const script = document.createElement("script");
		script.src = CHECKOUT_SRC;
		script.async = true;
		script.onload = () => (typeof window.Razorpay === "function" ? resolve() : reject(new Error("checkout")));
		script.onerror = () => {
			loading = null;
			reject(new Error("checkout"));
		};
		document.head.appendChild(script);
	});
	return loading;
}

/** Open the payment window. `onFailed` hears of a payment the bank refused (the window stays open). */
export async function openCheckout(
	options: RazorpayOptions,
	onFailed?: (description: string | undefined) => void,
): Promise<void> {
	await loadCheckout();
	const Razorpay = window.Razorpay;
	if (!Razorpay) throw new Error("checkout");
	const instance = new Razorpay(options);
	if (onFailed) instance.on("payment.failed", (r) => onFailed(r.error?.description));
	instance.open();
}
