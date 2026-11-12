import { useQuery } from "@tanstack/react-query";
import { CreditCard, Wallet } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { ownBillingQuery } from "@/features/billing/addons/ActiveAddOns";
import { paymentConfigQuery } from "@/features/payments/api";
import { goToPay } from "@/features/payments/checkout";
import { TaxDetailsDialog } from "@/features/payments/TaxDetailsDialog";
import type { ShopItem } from "@/lib/api/billing-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatMinor } from "@/lib/format/money";
import { en, fill } from "@/lib/strings/en";
import { shopStrings as s } from "@/lib/strings/shop";
import { newPurchaseKey, useBuyAddOn } from "./api";
import { priceLine } from "./ShopCard";

/**
 * "Buy {name}" (P5): pay online (a full page load to `/pay`, with the tax details asked for first
 * when the workspace has none) or from the balance (a service also takes a note for the order).
 * The balance is checked against what the owner can see: a purchase that would go past the pause
 * line is not offered, and the engine still refuses it (`insufficient_balance`) if it changed.
 */
export function ShopDialog({ item, onOpenChange }: { item: ShopItem | null; onOpenChange: (open: boolean) => void }) {
	const id = useId();
	const buy = useBuyAddOn();
	const config = useQuery({ ...paymentConfigQuery, retry: false });
	const own = useQuery(ownBillingQuery);
	const [note, setNote] = useState("");
	const [failure, setFailure] = useState<string | null>(null);
	const [details, setDetails] = useState(false);
	const key = useRef(newPurchaseKey());

	useEffect(() => {
		if (item) {
			key.current = newPurchaseKey();
			setNote("");
			setFailure(null);
		}
	}, [item]);

	const canOnline = item?.buy_with.includes("online") === true;
	const canBalance = item?.buy_with.includes("balance") === true;
	const available = own.data?.available_minor;
	// the engine knows the pause line (a purchase must stay above it), so it says what is affordable
	const tooLow =
		canBalance && item != null && (item.affordable === false || (available != null && available < item.price_minor));
	const isService = item?.kind === "service";

	const payOnline = () => {
		if (!item) return;
		if (config.data && !config.data.tax_details_complete) setDetails(true);
		else goToPay({ purpose: "add_on", addOn: item.id });
	};

	const payFromBalance = async () => {
		if (!item) return;
		setFailure(null);
		try {
			await buy.mutateAsync({
				id: item.id,
				body: { payment: "wallet", idempotency_key: key.current, ...(note.trim() ? { note: note.trim() } : {}) },
			});
			toast.success(fill(s.dialog.bought, { name: item.name }), {
				description: isService ? s.dialog.boughtService : s.dialog.boughtBody,
			});
			onOpenChange(false);
		} catch (e) {
			setFailure(messageFor(toApiError(e)).title);
		}
	};

	return (
		<>
			<Dialog open={item !== null} onOpenChange={onOpenChange}>
				<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-[500px]">
					{item ? (
						<div className="grid gap-4">
							<DialogHeader>
								<DialogTitle>{fill(s.dialog.title, { name: item.name })}</DialogTitle>
								<DialogDescription>{fill(s.dialog.lead, { gives: item.gives })}</DialogDescription>
							</DialogHeader>
							{failure ? <FormAlert>{failure}</FormAlert> : null}
							<p className="m-0 rounded-md border border-border bg-secondary px-3 py-2 text-[13.5px]">
								<b className="mr-1">{s.dialog.priceLabel}:</b>
								{priceLine(item)}
								{item.renews ? <small className="block text-muted-foreground">{s.dialog.renewsNote}</small> : null}
							</p>
							{canBalance && isService ? (
								<Field id={`${id}-note`} label={s.dialog.note} hint={s.dialog.noteHint}>
									<Textarea
										id={`${id}-note`}
										rows={3}
										maxLength={500}
										value={note}
										onChange={(e) => setNote(e.target.value)}
									/>
								</Field>
							) : null}
							<div className="grid gap-2">
								<span className="text-[13px] font-semibold">{s.dialog.howToPay}</span>
								{canOnline ? (
									<Button type="button" variant="outline" className="h-auto justify-start py-2.5" onClick={payOnline}>
										<CreditCard aria-hidden className="size-4" />
										<span className="grid text-left">
											<span>{s.dialog.online}</span>
											<small className="font-normal text-muted-foreground">{s.dialog.onlineHint}</small>
										</span>
									</Button>
								) : null}
								{canBalance ? (
									<>
										<Button
											type="button"
											variant="outline"
											className="h-auto justify-start py-2.5"
											disabled={tooLow || buy.isPending}
											onClick={() => void payFromBalance()}
										>
											<Wallet aria-hidden className="size-4" />
											<span className="grid text-left">
												<span>
													{buy.isPending
														? s.dialog.submitting
														: fill(s.dialog.submit, { price: formatMinor(item.price_minor, item.currency) })}
												</span>
												<small className="font-normal text-muted-foreground">
													{fill(s.dialog.balanceHint, { price: formatMinor(item.price_minor, item.currency) })}
												</small>
											</span>
										</Button>
										{tooLow ? (
											<small data-testid="shop-balance-low" className="text-warning">
												{s.dialog.balanceLow}
											</small>
										) : null}
									</>
								) : null}
							</div>
							<DialogFooter>
								<Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
									{en.common.cancel}
								</Button>
							</DialogFooter>
						</div>
					) : null}
				</DialogContent>
			</Dialog>
			<TaxDetailsDialog
				open={details}
				onOpenChange={setDetails}
				onSaved={() => item && goToPay({ purpose: "add_on", addOn: item.id })}
			/>
		</>
	);
}
