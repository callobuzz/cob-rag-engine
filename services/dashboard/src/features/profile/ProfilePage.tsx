import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useId } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { PageHeader } from "@/components/shell/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { FormFoot, Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import type { Principal } from "@/lib/auth/principal";
import { usePrincipal } from "@/lib/auth/store";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { profileStrings as t } from "@/lib/strings/accounts";
import { fill } from "@/lib/strings/en";
import { meQuery, useUpdateProfile } from "./api";
import { PasswordForm } from "./PasswordForm";
import { SessionsList } from "./SessionsList";

/** `/profile` (mockup `[data-view=profile]`): name, password, sessions — email sign-ins only. */
export function ProfilePage() {
	const principal = usePrincipal();
	return (
		<>
			<PageHeader title={t.page.title} description={t.page.lead} />
			{principal?.auth === "api_key" ? (
				<KeyPrincipal principal={principal} />
			) : (
				<div className="grid items-start gap-4 min-[1101px]:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
					<div className="grid min-w-0 content-start gap-4">
						<ProfileCard />
						<PasswordForm />
					</div>
					<SessionsList />
				</div>
			)}
		</>
	);
}

/** A key has no profile, password or sessions (`/auth/*` answers `session_required`). */
function KeyPrincipal({ principal }: { principal: Principal }) {
	return (
		<Panel title={t.keyPrincipal.title}>
			<div className="grid gap-2 p-4 text-[13.5px] text-foreground-2" data-testid="key-principal">
				<p className="m-0 flex items-start gap-2">
					<KeyRound className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
					<span>
						{principal.keyKind === "admin"
							? t.keyPrincipal.admin
							: fill(t.keyPrincipal.tenant, { tenant: principal.tenant?.name ?? "" })}
					</span>
				</p>
				<p className="m-0">{t.keyPrincipal.body}</p>
				<p className="m-0">{t.keyPrincipal.hint}</p>
			</div>
		</Panel>
	);
}

function ProfileCard() {
	const principal = usePrincipal();
	const me = useQuery(meQuery);
	const user = me.data?.user ?? principal?.user;
	return user ? <ProfileForm key={user.name} name={user.name} email={user.email} /> : null;
}

const nameSchema = z.object({
	name: z.string().trim().min(1, t.profile.nameRequired).max(255, t.profile.nameLong),
});

function ProfileForm({ name: initial, email }: { name: string; email: string }) {
	const ids = { name: useId(), email: useId() };
	const update = useUpdateProfile();
	const form = useAppForm<{ name: string }>({
		resolver: zodResolver(nameSchema),
		defaultValues: { name: initial },
	});
	const submit = useAppSubmit(form, async (v) => {
		await update.mutateAsync(v.name.trim());
		toast.success(t.profile.savedToast, { description: t.profile.savedToastBody });
	});
	const error = form.formState.errors.name?.message;
	const unchanged = form.watch("name").trim() === initial;

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate>
			<Panel
				title={t.profile.title}
				sub={t.profile.sub}
				aside={
					<Badge variant="ok">
						<span aria-hidden className="size-1.5 rounded-full bg-current" />
						{t.profile.verified}
					</Badge>
				}
			>
				<div className="grid gap-4 p-4">
					{submit.error ? <FormAlert>{messageFor(submit.error).title}</FormAlert> : null}
					<div className="grid gap-4 min-[861px]:grid-cols-2">
						<Field id={ids.name} label={t.profile.name} error={error}>
							<Input
								id={ids.name}
								autoComplete="name"
								aria-invalid={!!error || undefined}
								aria-describedby={describedBy(ids.name, { error: !!error })}
								{...form.register("name")}
							/>
						</Field>
						<Field id={ids.email} label={t.profile.email} hint={t.profile.emailHint}>
							<Input
								id={ids.email}
								readOnly
								value={email}
								className="font-mono"
								aria-describedby={describedBy(ids.email, { hint: true })}
							/>
						</Field>
					</div>
				</div>
				<FormFoot>
					<SubmitButton
						variant="default"
						pending={submit.isSubmitting}
						locked={unchanged}
						pendingLabel={t.profile.saving}
					>
						{t.profile.save}
					</SubmitButton>
				</FormFoot>
			</Panel>
		</form>
	);
}
