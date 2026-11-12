import { zodResolver } from "@hookform/resolvers/zod";
import { useId, useState } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { ErrorAlert } from "@/components/auth/ErrorAlert";
import { describedBy, Field } from "@/components/auth/Field";
import { NewPasswordFields, passwordShape, withPasswordRules } from "@/components/auth/NewPasswordFields";
import { PasswordField } from "@/components/auth/PasswordField";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { FormFoot, Panel } from "@/features/kbs/ui";
import { usePrincipal } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { profileStrings as t } from "@/lib/strings/accounts";
import { useChangePassword } from "./api";

/**
 * Change password (mockup `#pwForm`): current, new ×2 with the live checklist and meter → `POST
 * /auth/change-password`. The engine ends every other session; this one stays.
 */
export function PasswordForm() {
	const currentId = useId();
	const { passwordPolicy } = useBranding();
	const email = usePrincipal()?.user?.email;
	const change = useChangePassword();
	const [schema] = useState(() =>
		withPasswordRules(
			z.object({ current: z.string().min(1, t.password.currentRequired), ...passwordShape(passwordPolicy) }),
			{ email: () => email },
		),
	);
	const form = useAppForm<{ current: string; password: string; repeat: string }>({
		resolver: zodResolver(schema),
		defaultValues: { current: "", password: "", repeat: "" },
	});
	const currentError = form.formState.errors.current?.message;
	const submit = useAppSubmit(
		form,
		async (v) => {
			await change.mutateAsync({ current_password: v.current, new_password: v.password });
			toast.success(t.password.toast, { description: t.password.toastBody });
			form.reset({ current: "", password: "", repeat: "" });
		},
		{
			passwordPolicy,
			onError: (err) => {
				if (err.code !== "invalid_credentials") return false;
				// The current password is wrong: that is about its field, and it clears on edit.
				form.setError("current", { type: "server", message: t.password.currentWrong });
				form.setFocus("current");
				return true;
			},
		},
	);

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate>
			<Panel title={t.password.title} sub={t.password.sub}>
				<div className="grid gap-4 p-4">
					{submit.error ? <ErrorAlert error={submit.error} until={submit.until} /> : null}
					<Field id={currentId} label={t.password.current} error={currentError}>
						<PasswordField
							id={currentId}
							autoComplete="current-password"
							aria-invalid={!!currentError || undefined}
							aria-describedby={describedBy(currentId, { error: !!currentError })}
							{...form.register("current")}
						/>
					</Field>
					<NewPasswordFields form={form} policy={passwordPolicy} email={email} label={t.password.newPassword} />
				</div>
				<FormFoot>
					<SubmitButton
						variant="default"
						pending={submit.isSubmitting}
						locked={submit.locked}
						pendingLabel={t.password.submitting}
					>
						{t.password.submit}
					</SubmitButton>
				</FormFoot>
			</Panel>
		</form>
	);
}
