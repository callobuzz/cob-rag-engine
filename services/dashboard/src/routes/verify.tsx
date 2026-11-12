import { zodResolver } from "@hookform/resolvers/zod";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Mail } from "lucide-react";
import { useId, useState } from "react";
import { Controller } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { AuthLayout } from "@/components/auth/AuthLayout";
import { CodeInput } from "@/components/auth/CodeInput";
import { ErrorAlert } from "@/components/auth/ErrorAlert";
import { FormAlert } from "@/components/auth/FormAlert";
import { ResendButton, useResend } from "@/components/auth/ResendButton";
import { StatusCard } from "@/components/auth/StatusCard";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { api, unwrap } from "@/lib/api/client";
import { messageFor } from "@/lib/api/error-messages";
import { completeSignIn } from "@/lib/auth/actions";
import { redirectIfSignedIn } from "@/lib/auth/guards";
import { clearPendingEmail, readPendingEmail } from "@/lib/auth/storage";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { en, fill } from "@/lib/strings/en";

export const Route = createFileRoute("/verify")({
	beforeLoad: ({ context }) => redirectIfSignedIn({ context }),
	component: VerifyPage,
});

const schema = z.object({ code: z.string().regex(/^\d{6}$/, "Enter the 6-digit code from the email.") });
type Values = z.infer<typeof schema>;

function VerifyPage() {
	const navigate = useNavigate();
	const [email] = useState(readPendingEmail);
	const [shake, setShake] = useState(0);
	const [focusSignal, setFocusSignal] = useState(0);
	const resend = useResend(email, "verify");
	const alertId = useId();
	const codeId = useId();
	const form = useAppForm<Values>({ resolver: zodResolver(schema), defaultValues: { code: "" } });
	const codeError = form.formState.errors.code?.message;

	const submit = useAppSubmit(
		form,
		async ({ code }) => {
			const t = await unwrap(api().POST("/api/v1/auth/verify", { body: { email, code } }));
			clearPendingEmail();
			const p = completeSignIn(t, false);
			if (p.tenant && p.tenant.status !== "active") {
				await navigate({ to: "/status" });
				return;
			}
			if (p.tenant) toast.success(fill(en.login.welcome, { tenant: p.tenant.name }));
			// P6 plan choice: a plan with a fee chosen on the pricing page starts once it is paid
			if (t.plan_choice?.status === "awaiting_payment") {
				await navigate({ to: "/plan" });
				return;
			}
			await navigate({ to: "/" });
		},
		{
			onError: (err) => {
				if (err.code !== "invalid_code") return false;
				// A wrong code belongs to the code field; start the boxes over.
				setShake((n) => n + 1);
				form.setValue("code", "");
				form.setError("code", { type: "server", message: messageFor(err).title });
				setFocusSignal((n) => n + 1);
				return true;
			},
		},
	);

	if (!email) {
		return (
			<AuthLayout title={en.verify.title}>
				<StatusCard icon={Mail} tone="mail" title={en.verify.title}>
					<FormAlert tone="info">{en.verify.noEmail}</FormAlert>
					<Link to="/signup" className="font-semibold text-brand-text">
						{en.verify.goBack}
					</Link>
				</StatusCard>
			</AuthLayout>
		);
	}

	return (
		<AuthLayout title={en.verify.title}>
			<form className="grid gap-4" noValidate onSubmit={(e) => void submit.onSubmit(e)}>
				<span className="grid size-[52px] place-items-center rounded-[14px] bg-primary-soft text-primary">
					<Mail className="size-[26px]" aria-hidden />
				</span>
				<h1 className="text-2xl font-bold">{en.verify.title}</h1>
				<p className="-mt-2 mb-0 text-foreground-2">
					{en.verify.lead} <b className="text-foreground">{email}</b>.
				</p>
				{submit.error ? (
					<ErrorAlert
						id={alertId}
						error={submit.error}
						until={submit.until}
						onAction={(id) => {
							if (id === "resend") void resend.send();
						}}
					/>
				) : null}
				<div className="grid gap-1.5">
					<Controller
						control={form.control}
						name="code"
						render={({ field }) => (
							<CodeInput
								key={shake}
								value={field.value}
								onChange={field.onChange}
								onBlur={field.onBlur}
								inputRef={field.ref}
								onComplete={() => void submit.onSubmit()}
								invalid={!!codeError}
								disabled={submit.isSubmitting}
								label={en.verify.codeGroup}
								describedBy={codeError ? `${codeId}-error` : submit.error ? alertId : undefined}
								focusSignal={focusSignal}
								autoFocus
							/>
						)}
					/>
					{codeError ? (
						<small id={`${codeId}-error`} className="text-[12.5px] font-medium text-destructive">
							{codeError}
						</small>
					) : null}
				</div>
				<div className="flex flex-wrap justify-between gap-3 text-[12.5px] text-muted-foreground">
					<span>{en.verify.rules}</span>
					<ResendButton resend={resend} />
				</div>
				<SubmitButton variant="brand" size="lg" pending={submit.isSubmitting} locked={submit.locked}>
					{en.verify.submit}
				</SubmitButton>
				<p className="m-0 text-center text-[13.5px] text-foreground-2">
					{en.verify.wrong}{" "}
					<Link to="/signup" className="font-semibold text-brand-text">
						{en.verify.goBack}
					</Link>
				</p>
			</form>
		</AuthLayout>
	);
}
