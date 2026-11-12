import { zodResolver } from "@hookform/resolvers/zod";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { MessageCircle, UserX } from "lucide-react";
import { useId, useState } from "react";
import { z } from "zod";
import { AuthLayout } from "@/components/auth/AuthLayout";
import { ContactBox } from "@/components/auth/ContactBox";
import { ErrorAlert } from "@/components/auth/ErrorAlert";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { NewPasswordFields, passwordShape, withPasswordRules } from "@/components/auth/NewPasswordFields";
import { StatusCard } from "@/components/auth/StatusCard";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { BackToWebsite } from "@/features/landing/BackToWebsite"; // LP4 landing
import { SignupAgreement, useSignupLegal } from "@/features/legal/SignupAgreement"; // P3 legal
import { ChosenPlan, useChosenPlan } from "@/features/plan-choice/SignupPlan"; // P6 plan choice
import { api, unwrap } from "@/lib/api/client";
import { redirectIfSignedIn } from "@/lib/auth/guards";
import { writePendingEmail } from "@/lib/auth/storage";
import { safeHttpUrl, useBranding } from "@/lib/branding/BrandingProvider";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { emailSchema } from "@/lib/forms/email-rule";
import { en, fill } from "@/lib/strings/en";

export const Route = createFileRoute("/signup")({
	// P6 plan choice: `?plan=<slug or id>` from the pricing page
	validateSearch: (search: Record<string, unknown>): { plan?: string } =>
		typeof search.plan === "string" && search.plan.length <= 64 ? { plan: search.plan } : {},
	beforeLoad: ({ context }) => redirectIfSignedIn({ context }),
	component: SignupPage,
});

type Values = { business_name: string; name: string; email: string; password: string };

function LogInLine() {
	return (
		<p className="m-0 text-center text-[13.5px] text-foreground-2">
			{en.signup.haveAccount}{" "}
			<Link to="/login" className="font-semibold text-brand-text">
				{en.signup.logIn}
			</Link>
		</p>
	);
}

/** Closed mode (mockup `[data-auth=closed]`): contact text, "Contact us" to contact_url, support. */
function ClosedView({ text, url }: { text: string; url: string | null }) {
	return (
		<div className="grid gap-4">
			<StatusCard icon={MessageCircle} tone="talk" title={en.signup.closedTitle}>
				<p className="m-0 text-foreground-2">{text}</p>
				{url ? (
					<Button asChild variant="brand" size="lg">
						<a href={url} target="_blank" rel="noopener noreferrer">
							{en.signup.contact}
						</a>
					</Button>
				) : null}
				<ContactBox />
			</StatusCard>
			<LogInLine />
		</div>
	);
}

function SignupPage() {
	const b = useBranding();
	const navigate = useNavigate();
	const policy = b.passwordPolicy;
	const [closed, setClosed] = useState<{ text: string; url: string | null } | null>(null);
	const ids = { business: useId(), name: useId(), email: useId(), alert: useId(), agree: useId() };
	// P3 legal: with the Terms and the Privacy notice published, sign-up needs the box ticked
	const legal = useSignupLegal();
	const [agreed, setAgreed] = useState(false);
	const [agreeError, setAgreeError] = useState(false);
	// /P3 legal
	const chosen = useChosenPlan(Route.useSearch().plan); // P6 plan choice

	const [schema] = useState(() =>
		withPasswordRules(
			z.object({
				business_name: z.string().trim().min(1, "Enter your business name.").max(255),
				name: z.string().trim().min(1, "Enter your name.").max(255),
				email: emailSchema(b.specialUseEmailsAllowed),
				password: passwordShape(policy).password,
			}),
			{ email: (v) => (typeof v.email === "string" ? v.email : undefined), repeat: false },
		),
	);
	const form = useAppForm<Values>({
		resolver: zodResolver(schema),
		defaultValues: { business_name: "", name: "", email: "", password: "" },
	});
	const errs = form.formState.errors;
	const email = form.watch("email");
	const submit = useAppSubmit(
		form,
		async (v) => {
			const body = {
				...v,
				...(legal.accept ? { accept_legal: legal.accept } : {}),
				...(chosen ? { plan: chosen.id } : {}), // P6 plan choice
			};
			const res = await unwrap(api().POST("/api/v1/auth/signup", { body }));
			// The normalized address from the response, never the typed one (D1 decision).
			writePendingEmail(res.email);
			await navigate({ to: "/verify" });
		},
		{
			passwordPolicy: policy,
			onError: (err) => {
				// P3 legal: the pages changed while the form was open: show the new ones, ask again
				if (err.code === "legal_version_changed") {
					legal.refetch();
					setAgreed(false);
					return false;
				}
				// /P3 legal
				if (err.code !== "signup_closed") return false;
				setClosed({
					text: err.detail || b.contactText,
					url: safeHttpUrl(typeof err.extra.contact_url === "string" ? err.extra.contact_url : null),
				});
				return true;
			},
		},
	);
	const { error } = submit;

	if (closed || b.signupMode === "closed") {
		return (
			<AuthLayout title={en.signup.closedTitle}>
				<ClosedView text={closed?.text ?? b.contactText} url={closed ? closed.url : b.contactUrl} />
			</AuthLayout>
		);
	}
	if (!b.signupAvailable) {
		return (
			<AuthLayout title={en.signup.unavailableTitle}>
				<StatusCard icon={UserX} tone="wait" title={en.signup.unavailableTitle}>
					<p className="m-0 text-foreground-2">{en.signup.unavailableBody}</p>
					<ContactBox />
				</StatusCard>
				<LogInLine />
			</AuthLayout>
		);
	}

	return (
		<AuthLayout title={en.signup.title}>
			{/* LP4 landing */}
			<BackToWebsite />
			{/* /LP4 landing */}
			<form
				className="grid gap-4"
				noValidate
				onSubmit={(e) => {
					// P3 legal: the box comes first; the other fields still show what to fix
					if (legal.required && !agreed) {
						e.preventDefault();
						setAgreeError(true);
						void form.trigger();
						return;
					}
					// /P3 legal
					void submit.onSubmit(e);
				}}
			>
				<h1 className="text-2xl font-bold">{en.signup.title}</h1>
				<p className="-mt-2 mb-0 text-foreground-2">{en.signup.lead}</p>
				{error ? <ErrorAlert id={ids.alert} error={error} until={submit.until} /> : null}
				{chosen ? <ChosenPlan plan={chosen} /> : null} {/* P6 plan choice */}
				<Field id={ids.business} label={en.signup.business} error={errs.business_name?.message}>
					<Input
						id={ids.business}
						autoComplete="organization"
						aria-invalid={!!errs.business_name || undefined}
						aria-describedby={describedBy(ids.business, { error: !!errs.business_name })}
						{...form.register("business_name")}
					/>
				</Field>
				<Field id={ids.name} label={en.signup.name} error={errs.name?.message}>
					<Input
						id={ids.name}
						autoComplete="name"
						aria-invalid={!!errs.name || undefined}
						aria-describedby={describedBy(ids.name, { error: !!errs.name })}
						{...form.register("name")}
					/>
				</Field>
				<Field id={ids.email} label={en.signup.email} hint={en.signup.emailHint} error={errs.email?.message}>
					<Input
						id={ids.email}
						type="email"
						autoComplete="email"
						aria-invalid={!!errs.email || undefined}
						aria-describedby={describedBy(ids.email, { hint: true, error: !!errs.email })}
						{...form.register("email")}
					/>
				</Field>
				<NewPasswordFields form={form} policy={policy} email={email} label={en.signup.password} withRepeat={false} />
				{/* P3 legal */}
				<SignupAgreement
					legal={legal}
					checked={agreed}
					error={agreeError && !agreed}
					id={ids.agree}
					onChange={(v) => {
						setAgreed(v);
						if (v) setAgreeError(false);
					}}
				/>
				{/* /P3 legal */}
				{b.signupMode === "approval" ? (
					<FormAlert tone="info">{fill(en.signup.approval, { product: b.productName })}</FormAlert>
				) : null}
				<SubmitButton variant="brand" size="lg" pending={submit.isSubmitting} locked={submit.locked}>
					{en.signup.submit}
				</SubmitButton>
				<LogInLine />
			</form>
		</AuthLayout>
	);
}
