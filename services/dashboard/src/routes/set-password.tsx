import { zodResolver } from "@hookform/resolvers/zod";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { KeyRound } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { AuthLayout } from "@/components/auth/AuthLayout";
import { ErrorAlert } from "@/components/auth/ErrorAlert";
import { ExpiredLinkHint } from "@/components/auth/ExpiredLinkHint";
import { FormAlert } from "@/components/auth/FormAlert";
import {
	NewPasswordFields,
	type NewPasswordValues,
	passwordShape,
	withPasswordRules,
} from "@/components/auth/NewPasswordFields";
import { StatusCard } from "@/components/auth/StatusCard";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { api, unwrap } from "@/lib/api/client";
import { takeSecretFragment } from "@/lib/auth/fragment";
import { writePendingEmail } from "@/lib/auth/storage";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { en, fill } from "@/lib/strings/en";

/**
 * Super-admin first password (A3 bootstrap link `/set-password#email=…&code=…`). The fragment
 * was read and removed from the address bar by captureSecretFragment() before any request.
 */
export const Route = createFileRoute("/set-password")({
	component: SetPasswordPage,
});

function SetPasswordPage() {
	const navigate = useNavigate();
	const { passwordPolicy } = useBranding();
	const [secret] = useState(() => {
		const p = takeSecretFragment("/set-password");
		return p?.email && p.code ? { email: p.email, code: p.code } : null;
	});
	const [schema] = useState(() =>
		withPasswordRules(z.object(passwordShape(passwordPolicy)), { email: () => secret?.email }),
	);
	const form = useAppForm<NewPasswordValues>({
		resolver: zodResolver(schema),
		defaultValues: { password: "", repeat: "" },
	});
	const submit = useAppSubmit(
		form,
		async (v) => {
			if (!secret) return;
			await unwrap(
				api().POST("/api/v1/auth/reset", {
					body: { email: secret.email, code: secret.code, new_password: v.password },
				}),
			);
			writePendingEmail(secret.email);
			toast.success(en.setPassword.done);
			await navigate({ to: "/login" });
		},
		{ passwordPolicy },
	);

	if (!secret) {
		return (
			<AuthLayout title={en.setPassword.title}>
				<StatusCard icon={KeyRound} tone="wait" title={en.setPassword.title}>
					<FormAlert tone="warn">{en.setPassword.missing}</FormAlert>
					<Link to="/login" className="font-semibold text-brand-text">
						{en.common.backToLogin}
					</Link>
				</StatusCard>
			</AuthLayout>
		);
	}

	return (
		<AuthLayout title={en.setPassword.title}>
			<form className="grid gap-4" noValidate onSubmit={(e) => void submit.onSubmit(e)}>
				<h1 className="text-2xl font-bold">{en.setPassword.title}</h1>
				<p className="-mt-2 mb-0 text-foreground-2">{fill(en.setPassword.lead, { email: secret.email })}</p>
				{submit.error ? <ErrorAlert error={submit.error} until={submit.until} /> : null}
				{submit.error?.code === "code_expired" ? <ExpiredLinkHint /> : null}
				<NewPasswordFields form={form} policy={passwordPolicy} email={secret.email} />
				<SubmitButton variant="brand" size="lg" pending={submit.isSubmitting} locked={submit.locked}>
					{en.setPassword.submit}
				</SubmitButton>
			</form>
		</AuthLayout>
	);
}
