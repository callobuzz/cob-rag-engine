import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { CopyButton } from "@/components/patterns/CopyButton";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { tenantsQuery } from "@/features/customers/api";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatDate } from "@/lib/format/date";
import { parseMajor } from "@/lib/format/money";
import { en, fill } from "@/lib/strings/en";
import { paymentStrings } from "@/lib/strings/payments";
import { type PaymentLinkBody, useCreatePaymentLink } from "../api";

const s = paymentStrings.admin.link;

/** Send a customer a link to pay (P1): top-up or plan fee. The link can be copied for WhatsApp. */
export function PaymentLinkDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
	const id = useId();
	const tenants = useQuery({ ...tenantsQuery({ status: "active", offset: 0, limit: 100 }), enabled: open });
	const [tenantId, setTenantId] = useState("");
	const [purpose, setPurpose] = useState<PaymentLinkBody["purpose"]>("top_up");
	const [amount, setAmount] = useState("");
	const [email, setEmail] = useState(true);
	const [failure, setFailure] = useState<string | null>(null);
	const [made, setMade] = useState<{ url: string; until: string; emailed: boolean } | null>(null);
	const create = useCreatePaymentLink(tenantId);

	useEffect(() => {
		if (!open) return;
		setTenantId("");
		setPurpose("top_up");
		setAmount("");
		setEmail(true);
		setFailure(null);
		setMade(null);
	}, [open]);

	const submit = async () => {
		setFailure(null);
		const minor = purpose === "top_up" ? parseMajor(amount) : null;
		if (!tenantId || (purpose === "top_up" && (minor === null || minor < 1))) {
			setFailure(s.pick);
			return;
		}
		try {
			const res = await create.mutateAsync({
				purpose,
				amount_minor: minor,
				add_on_id: null,
				send_email: email,
			});
			setMade({ url: res.short_url, until: res.expires_at, emailed: res.emailed });
		} catch (e) {
			setFailure(messageFor(toApiError(e)).title);
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-[500px]" data-testid="payment-link-dialog">
				{made ? (
					<div className="grid gap-4" data-testid="payment-link-made">
						<DialogHeader>
							<DialogTitle>{s.ready}</DialogTitle>
							<DialogDescription>{fill(s.readyBody, { date: formatDate(made.until) })}</DialogDescription>
						</DialogHeader>
						<div className="flex flex-wrap items-center gap-2">
							<code className="min-w-0 break-all rounded-sm bg-muted px-2 py-1 text-[12.5px]">{made.url}</code>
							<CopyButton value={made.url} label={s.copy} />
						</div>
						<p className="m-0 text-[13px] text-muted-foreground">{made.emailed ? s.emailed : s.notEmailed}</p>
						<DialogFooter>
							<Button type="button" variant="default" onClick={() => onOpenChange(false)}>
								{en.common.close}
							</Button>
						</DialogFooter>
					</div>
				) : (
					<form
						noValidate
						className="grid gap-4"
						onSubmit={(e) => {
							e.preventDefault();
							void submit();
						}}
					>
						<DialogHeader>
							<DialogTitle>{s.title}</DialogTitle>
							<DialogDescription>{s.lead}</DialogDescription>
						</DialogHeader>
						{failure ? <FormAlert>{failure}</FormAlert> : null}
						<Field id={`${id}-tenant`} label={s.workspace}>
							<NativeSelect id={`${id}-tenant`} value={tenantId} onChange={(e) => setTenantId(e.target.value)}>
								<option value="">{s.pick}</option>
								{(tenants.data?.items ?? []).map((t) => (
									<option key={t.id} value={t.id}>
										{t.name}
									</option>
								))}
							</NativeSelect>
						</Field>
						<Field id={`${id}-purpose`} label={s.purpose}>
							<NativeSelect
								id={`${id}-purpose`}
								value={purpose}
								onChange={(e) => setPurpose(e.target.value as PaymentLinkBody["purpose"])}
							>
								<option value="top_up">{s.topUp}</option>
								<option value="plan">{s.plan}</option>
							</NativeSelect>
						</Field>
						{purpose === "top_up" ? (
							<Field id={`${id}-amount`} label={s.amount}>
								<Input
									id={`${id}-amount`}
									inputMode="decimal"
									autoComplete="off"
									value={amount}
									onChange={(e) => setAmount(e.target.value)}
								/>
							</Field>
						) : null}
						<label className="flex items-center gap-2 text-[13px]">
							<input type="checkbox" checked={email} onChange={(e) => setEmail(e.target.checked)} />
							{s.email}
						</label>
						<DialogFooter>
							<Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
								{en.common.cancel}
							</Button>
							<SubmitButton variant="default" pending={create.isPending} pendingLabel={s.creating}>
								{s.create}
							</SubmitButton>
						</DialogFooter>
					</form>
				)}
			</DialogContent>
		</Dialog>
	);
}
