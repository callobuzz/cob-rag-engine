import { zodResolver } from "@hookform/resolvers/zod";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useId, useState } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { AuthLayout } from "@/components/auth/AuthLayout";
import { ErrorAlert } from "@/components/auth/ErrorAlert";
import { describedBy, Field } from "@/components/auth/Field";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Input } from "@/components/ui/input";
import { api, unwrap } from "@/lib/api/client";
import { redirectIfSignedIn } from "@/lib/auth/guards";
import { readPendingEmail, writePendingEmail } from "@/lib/auth/storage";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { emailSchema } from "@/lib/forms/email-rule";
import { en } from "@/lib/strings/en";

export const Route = createFileRoute("/forgot")({
	beforeLoad: ({ context }) => redirectIfSignedIn({ context }),
	component: ForgotPage,
});

// Shape only: the reserved-name rule is for creating addresses, not for finding an existing account.
const buildSchema = () => z.object({ email: emailSchema(true) });
type Values = z.infer<ReturnType<typeof buildSchema>>;

function ForgotPage() {
	const navigate = useNavigate();
	const id = useId();
	const [schema] = useState(() => buildSchema());
	const form = useAppForm<Values>({ resolver: zodResolver(schema), defaultValues: { email: readPendingEmail() } });
	const err = form.formState.errors.email?.message;
	const submit = useAppSubmit(form, async (v) => {
		const email = v.email.trim();
		await unwrap(api().POST("/api/v1/auth/forgot", { body: { email } }));
		writePendingEmail(email);
		toast.success(en.reset.sent);
		await navigate({ to: "/reset" });
	});

	return (
		<AuthLayout title={en.forgot.title}>
			<form className="grid gap-4" noValidate onSubmit={(e) => void submit.onSubmit(e)}>
				<h1 className="text-2xl font-bold">{en.forgot.title}</h1>
				<p className="-mt-2 mb-0 text-foreground-2">{en.forgot.lead}</p>
				{submit.error ? <ErrorAlert error={submit.error} until={submit.until} /> : null}
				<Field id={id} label={en.login.email} error={err}>
					<Input
						id={id}
						type="email"
						autoComplete="email"
						aria-invalid={!!err || undefined}
						aria-describedby={describedBy(id, { error: !!err })}
						{...form.register("email")}
					/>
				</Field>
				<SubmitButton variant="brand" size="lg" pending={submit.isSubmitting} locked={submit.locked}>
					{en.forgot.submit}
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
