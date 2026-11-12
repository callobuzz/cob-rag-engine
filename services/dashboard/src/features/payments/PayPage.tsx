import { useQuery } from "@tanstack/react-query";
import { getRouteApi, Link } from "@tanstack/react-router";
import { CircleCheck, LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/button";
import { useFooterLinks } from "@/features/legal/LegalLinks";
import { messageFor } from "@/lib/api/error-messages";
import { isApiError, toApiError } from "@/lib/api/errors";
import { usePrincipal } from "@/lib/auth/store";
import { formatMinor } from "@/lib/format/money";
import { fill } from "@/lib/strings/en";
import { paymentStrings } from "@/lib/strings/payments";
import {
	type OrderQuote,
	ownPaymentsQuery,
	paymentConfigQuery,
	quoteQuery,
	useCreateOrder,
	useVerifyPayment,
} from "./api";
import { openCheckout, type RazorpayOptions } from "./checkout";
import { TaxDetailsDialog } from "./TaxDetailsDialog";

const s = paymentStrings.pay;
const route = getRouteApi("/_app/pay");
const WATCH_MS = 60_000;
const money = (n: number) => formatMinor(n, "INR");

type Phase =
	| { kind: "ready" }
	| { kind: "opening" }
	| { kind: "confirming"; startedAt: number }
	| { kind: "done"; balance: number | null }
	| { kind: "late" };

/** "₹1,000 for your balance + ₹180 GST = ₹1,180 to pay", worded for what is being paid. */
export function breakdown(q: OrderQuote): string {
	const taxed = q.tax_minor > 0;
	const vars = {
		base: money(q.taxable_minor),
		tax: money(q.tax_minor),
		total: money(q.gross_minor),
		name: q.description,
	};
	if (q.purpose === "plan") return fill(taxed ? s.youPayPlan : s.youPayPlanNoTax, vars);
	if (q.purpose === "add_on") return fill(taxed ? s.youPayAddOn : s.youPayAddOnNoTax, vars);
	return fill(taxed ? s.youPay : s.youPayNoTax, vars);
}

/**
 * `/pay` (owners): the one page that loads Razorpay's checkout script (the dashboard's nginx gives
 * it its own CSP; every way here is a full page load). It shows what will be paid, GST included,
 * opens the payment window, hands the result to the engine (which asks Razorpay what really
 * happened) and then says the money is in the balance, or that it is still being confirmed.
 */
export function PayPage() {
	const { purpose = "top_up", amount, add_on: addOn } = route.useSearch();
	const principal = usePrincipal();
	const isMember = principal?.auth === "session" && principal.role === "member";
	const config = useQuery({ ...paymentConfigQuery, retry: false });
	const needsAmount = purpose === "top_up";
	const wantsQuote = config.data?.enabled === true && config.data.tax_details_complete && !(needsAmount && !amount);
	const quote = useQuery({ ...quoteQuery(purpose, amount ?? null, addOn ?? null), enabled: wantsQuote });
	const [phase, setPhase] = useState<Phase>({ kind: "ready" });
	const [failure, setFailure] = useState<string | null>(null);
	const [editing, setEditing] = useState(false);
	const key = useRef(crypto.randomUUID());
	const before = useRef<number | null>(null);
	const createOrder = useCreateOrder();
	const verify = useVerifyPayment();
	const links = useFooterLinks();
	const confirming = phase.kind === "confirming";
	const own = useQuery({ ...ownPaymentsQuery(confirming ? 3000 : false), enabled: config.data?.enabled === true });
	if (before.current === null && own.data) before.current = own.data.total;

	// the engine confirms a payment the browser could not (webhook, sweep): watch for it
	useEffect(() => {
		if (phase.kind !== "confirming") return;
		if (before.current != null && own.data && own.data.total > before.current) {
			setPhase({ kind: "done", balance: null });
			return;
		}
		const left = phase.startedAt + WATCH_MS - Date.now();
		if (left <= 0) {
			setPhase({ kind: "late" });
			return;
		}
		const timer = window.setTimeout(() => setPhase((p) => (p.kind === "confirming" ? { kind: "late" } : p)), left);
		return () => window.clearTimeout(timer);
	}, [phase, own.data]);

	const q = quote.data;
	const terms = links.find((l) => l.slug === "terms");
	const refunds = links.find((l) => l.slug === "refunds");

	const pay = async () => {
		if (!q) return;
		setFailure(null);
		setPhase({ kind: "opening" });
		try {
			const order = await createOrder.mutateAsync({
				purpose,
				amount_minor: amount ?? null,
				add_on_id: addOn ?? null,
				idempotency_key: key.current,
			});
			const options = {
				...(order.checkout as unknown as RazorpayOptions),
				handler: (res: { razorpay_payment_id: string; razorpay_signature: string }) => {
					void verify
						.mutateAsync({
							orderId: order.order_id,
							paymentId: res.razorpay_payment_id,
							signature: res.razorpay_signature,
						})
						.then((out) =>
							setPhase(
								out.status === "captured"
									? { kind: "done", balance: out.available_minor ?? null }
									: { kind: "confirming", startedAt: Date.now() },
							),
						)
						.catch(() => setPhase({ kind: "confirming", startedAt: Date.now() }));
				},
				modal: { ondismiss: () => setPhase((p) => (p.kind === "opening" ? { kind: "ready" } : p)) },
			};
			await openCheckout(options, (description) => setFailure(description ?? s.unavailable));
		} catch (e) {
			setPhase({ kind: "ready" });
			const err = toApiError(e);
			setFailure(isApiError(err) ? messageFor(err).title : s.unavailable);
		}
	};

	let body: React.ReactNode;
	if (isMember) {
		body = <FormAlert tone="info">{s.onlyOwner}</FormAlert>;
	} else if (config.isPending) {
		body = <CardSkeleton lines={3} />;
	} else if (config.isError) {
		body = <ErrorState error={config.error} title={s.unavailable} onRetry={() => void config.refetch()} />;
	} else if (!config.data.enabled) {
		body = <FormAlert tone="info">{s.off}</FormAlert>;
	} else if (needsAmount && !amount) {
		body = <FormAlert>{s.badAmount}</FormAlert>;
	} else if (!config.data.tax_details_complete) {
		body = (
			<div className="grid gap-3">
				<FormAlert tone="info">{s.needTaxDetails}</FormAlert>
				<div>
					<Button type="button" variant="default" onClick={() => setEditing(true)}>
						{s.addTaxDetails}
					</Button>
				</div>
			</div>
		);
	} else if (phase.kind === "done") {
		body = (
			<div role="status" data-testid="pay-done" className="grid gap-2">
				<p className="m-0 flex items-center gap-2 text-[18px] font-bold">
					<CircleCheck aria-hidden className="size-5 text-success" />
					{q && purpose === "top_up" ? fill(s.added, { amount: money(q.taxable_minor) }) : s.addedPlain}
				</p>
				{phase.balance != null ? (
					<p className="m-0 text-foreground-2">{fill(s.addedBody, { balance: money(phase.balance) })}</p>
				) : null}
			</div>
		);
	} else if (phase.kind === "confirming") {
		body = (
			<div role="status" data-testid="pay-confirming" className="grid gap-2">
				<p className="m-0 flex items-center gap-2 text-[16px] font-bold">
					<LoaderCircle aria-hidden className="size-4 animate-spin" />
					{s.confirming}
				</p>
				<p className="m-0 text-foreground-2">{s.confirmingBody}</p>
			</div>
		);
	} else if (phase.kind === "late") {
		body = (
			<div role="status" data-testid="pay-late" className="grid gap-2">
				<p className="m-0 text-[16px] font-bold">{s.stillConfirming}</p>
				<p className="m-0 text-foreground-2">{s.stillConfirmingBody}</p>
			</div>
		);
	} else if (quote.isError) {
		body = <ErrorState error={quote.error} title={s.unavailable} onRetry={() => void quote.refetch()} />;
	} else if (!q) {
		body = <CardSkeleton lines={3} />;
	} else {
		body = (
			<div className="grid gap-4">
				{failure ? <FormAlert>{failure}</FormAlert> : null}
				<p data-testid="pay-breakdown" className="m-0 text-[18px] font-bold">
					{breakdown(q)}
				</p>
				<p className="m-0 text-foreground-2">{s.howTo}</p>
				<div>
					<SubmitButton
						variant="default"
						pending={phase.kind === "opening"}
						pendingLabel={s.paying}
						onClick={() => void pay()}
					>
						{fill(s.payButton, { amount: money(q.gross_minor) })}
					</SubmitButton>
				</div>
				{terms ? (
					<p data-testid="pay-terms" className="m-0 text-[12.5px] text-muted-foreground">
						{s.termsLead}{" "}
						<a href={terms.url} target="_blank" rel="noopener noreferrer" className="underline">
							{terms.title}
						</a>
						{refunds ? (
							<>
								{" "}
								{s.termsAnd}{" "}
								<a href={refunds.url} target="_blank" rel="noopener noreferrer" className="underline">
									{refunds.title}
								</a>
							</>
						) : null}
						.
					</p>
				) : null}
			</div>
		);
	}

	return (
		<div className="grid max-w-[640px] gap-4" data-testid="pay-page">
			<PageHeader title={s.title} />
			{config.data?.mode === "test" ? (
				<p
					data-testid="pay-test-mode"
					className="m-0 rounded-sm bg-warning-soft px-3 py-2 text-[12.5px] font-bold tracking-wide text-warning"
				>
					{s.testMode}
				</p>
			) : null}
			<section className="rounded-lg border border-border bg-card p-4">{body}</section>
			<div>
				<Link to="/plan" className="text-[13.5px] underline underline-offset-2">
					{s.back}
				</Link>
			</div>
			<TaxDetailsDialog open={editing} onOpenChange={setEditing} onSaved={() => void config.refetch()} />
		</div>
	);
}
