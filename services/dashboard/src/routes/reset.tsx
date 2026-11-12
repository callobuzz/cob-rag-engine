import { zodResolver } from "@hookform/resolvers/zod";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useId, useState } from "react";
import { Controller } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { AuthLayout } from "@/components/auth/AuthLayout";
import { CodeInput } from "@/components/auth/CodeInput";
import { ErrorAlert } from "@/components/auth/ErrorAlert";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { NewPasswordFields, passwordShape, withPasswordRules } from "@/components/auth/NewPasswordFields";
import { ResendButton, useResend } from "@/components/auth/ResendButton";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Input } from "@/components/ui/input";
import { api, unwrap } from "@/lib/api/client";
import { messageFor } from "@/lib/api/error-messages";
import { redirectIfSignedIn } from "@/lib/auth/guards";
import { readPendingEmail, writePendingEmail } from "@/lib/auth/storage";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { emailSchema } from "@/lib/forms/email-rule";
import { en } from "@/lib/strings/en";

export const Route = createFileRoute("/reset")({
	beforeLoad: ({ context }) => redirectIfSignedIn({ context }),
	component: ResetPage,
});

interface Values {
	email: string;
	code: string;
	password: string;
	repeat: string;
}

function ResetPage() {
	const navigate = useNavigate();
	const { passwordPolicy } = useBranding();
	const [schema] = useState(() =>
		withPasswordRules(
			z.object({
				email: emailSchema(true), // shape only: this finds an existing account
				code: z.string().regex(/^\d{6}$/, "Enter the 6-digit code from the email."),
				...passwordShape(passwordPolicy),
			}),
			{ email: (v) => (typeof v.email === "string" ? v.email : undefined) },
		),
	);
	const [startedWithEmail] = useState(() => !!readPendingEmail());
	const [focusSignal, setFocusSignal] = useState(0);
	const form = useAppForm<Values>({
		resolver: zodResolver(schema),
		defaultValues: { email: readPendingEmail(), code: "", password: "", repeat: "" },
	});
	const email = form.watch("email");
	const resend = useResend(email.trim(), "reset", startedWithEmail);
	const ids = { email: useId(), code: useId(), alert: useId() };
	const errs = form.formState.errors;

	const submit = useAppSubmit(
		form,
		async (v) => {
			await unwrap(
				api().POST("/api/v1/auth/reset", {
					body: { email: v.email.trim(), code: v.code, new_password: v.password },
				}),
			);
			writePendingEmail(v.email.trim());
			toast.success(en.login.passwordChanged);
			await navigate({ to: "/login" });
		},
		{
			passwordPolicy,
			onError: (err) => {
				if (err.code !== "invalid_code") return false;
				// A wrong code is about the code field: say so there, and start the boxes over.
				form.setValue("code", "");
				form.setError("code", { type: "server", message: messageFor(err).title });
				setFocusSignal((n) => n + 1);
				return true;
			},
		},
	);

	return (
		<AuthLayout title={en.reset.title}>
			<form className="grid gap-4" noValidate onSubmit={(e) => void submit.onSubmit(e)}>
				<h1 className="text-2xl font-bold">{en.reset.title}</h1>
				<p className="-mt-2 mb-0 text-foreground-2">{en.reset.lead}</p>
				{submit.error ? (
					<ErrorAlert
						id={ids.alert}
						error={submit.error}
						until={submit.until}
						onAction={(id) => {
							if (id === "resend") void resend.send();
						}}
					/>
				) : null}
				<Field id={ids.email} label={en.login.email} error={errs.email?.message}>
					<Input
						id={ids.email}
						type="email"
						autoComplete="email"
						aria-invalid={!!errs.email || undefined}
						aria-describedby={describedBy(ids.email, { error: !!errs.email })}
						{...form.register("email")}
					/>
				</Field>
				<div className="grid gap-1.5">
					<span id={ids.code} className="text-[13px] font-semibold">
						Code
					</span>
					<Controller
						control={form.control}
						name="code"
						render={({ field }) => (
							<CodeInput
								value={field.value}
								onChange={field.onChange}
								onBlur={field.onBlur}
								inputRef={field.ref}
								invalid={!!errs.code}
								focusSignal={focusSignal}
								describedBy={describedBy(ids.code, { error: !!errs.code })}
							/>
						)}
					/>
					{errs.code ? (
						<small id={`${ids.code}-error`} className="text-[12.5px] font-medium text-destructive">
							{errs.code.message}
						</small>
					) : null}
					<div className="flex justify-end">
						<ResendButton resend={resend} />
					</div>
				</div>
				<NewPasswordFields form={form} policy={passwordPolicy} email={email} />
				<FormAlert tone="info">{en.reset.signsOut}</FormAlert>
				<SubmitButton variant="brand" size="lg" pending={submit.isSubmitting} locked={submit.locked}>
					{en.reset.submit}
				</SubmitButton>
				<p className="m-0 text-center text-[13.5px]">
					<Link to="/login" className="font-semibold text-brand-text">
						{en.common.backToLogin}
					</Link>
				</p>
			</form>
		</AuthLayout>
	);
}
