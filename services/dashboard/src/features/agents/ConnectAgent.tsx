import { zodResolver } from "@hookform/resolvers/zod";
import { Lock } from "lucide-react";
import { useId } from "react";
import { Controller } from "react-hook-form";
import { z } from "zod";
import { ErrorAlert } from "@/components/auth/ErrorAlert";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { SecretReveal } from "@/components/patterns/SecretReveal";
import { Button } from "@/components/ui/button";
import { Form2, Panel } from "@/features/kbs/ui";
import { KEY_PERMISSIONS, type KeyOwner, type KeyPermission, useCreateKeyFor } from "@/features/keys/api";
import { PermissionPicker } from "@/features/keys/PermissionPicker";
import type { Corpus } from "@/lib/api/queries";
import { usePrincipal } from "@/lib/auth/store";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { can } from "@/lib/permissions";
import { fill } from "@/lib/strings/en";
import { agentsStrings as t } from "@/lib/strings/widget";
import type { InstallMode } from "./configs";

/** What "Connect an agent" made: shown once, then forgotten with the page. */
export interface ConnectedAgent {
	name: string;
	key: string;
	permission: KeyPermission;
	target: "kb" | "customer";
}

const schema = z.object({
	target: z.enum(["kb", "customer"]),
	permission: z.enum(KEY_PERMISSIONS as [KeyPermission, ...KeyPermission[]]),
});
type ConnectForm = z.infer<typeof schema>;

/**
 * "Connect an agent": pick what the key reaches (this knowledge base, or all of the customer's)
 * and its permission (read by default), and create a key named "Agent – <client>". The key comes
 * back once and is put into the config below; creating it is the only way to see it.
 */
export function ConnectAgent({
	kb,
	clientLabel,
	install,
	onInstall,
	connected,
	onConnected,
	onForget,
}: {
	kb: Corpus;
	clientLabel: string;
	install: InstallMode;
	onInstall: (m: InstallMode) => void;
	connected: ConnectedAgent | null;
	onConnected: (c: ConnectedAgent) => void;
	onForget: () => void;
}) {
	const principal = usePrincipal();
	const owner = can(principal, "key.manage");
	const ids = { target: useId(), install: useId() };
	const create = useCreateKeyFor();
	const form = useAppForm<ConnectForm>({
		resolver: zodResolver(schema),
		defaultValues: { target: "kb", permission: "read" },
	});
	const ownerFor = (target: "kb" | "customer"): KeyOwner =>
		target === "kb"
			? { kind: "kb", id: kb.id, name: kb.name }
			: principal?.view === "admin"
				? { kind: "customer", id: kb.tenant_id, name: kb.tenant_name ?? "" }
				: { kind: "workspace", name: principal?.tenant?.name ?? kb.tenant_name ?? "" };
	const submit = useAppSubmit(form, async (v) => {
		const name = fill(t.connect.keyName, { client: clientLabel });
		const key = await create.mutateAsync({ owner: ownerFor(v.target), name, permission: v.permission });
		onConnected({ name: key.name, key: key.api_key, permission: v.permission, target: v.target });
	});
	const tenantName = kb.tenant_name ?? principal?.tenant?.name ?? "";

	return (
		<Panel title={t.connect.title} sub={t.connect.sub}>
			<div className="grid gap-4 p-4">
				{connected ? (
					<div className="grid gap-3" data-testid="connected-agent">
						<FormAlert tone="warn">
							<b className="font-semibold">{fill(t.connect.createdTitle, { name: connected.name })}</b>{" "}
							{t.connect.createdBody}
						</FormAlert>
						<SecretReveal value={connected.key} label={fill(t.connect.keyLabel, { name: connected.name })} />
						<div>
							<Button variant="outline" onClick={onForget}>
								{t.connect.forget}
							</Button>
						</div>
					</div>
				) : (
					<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
						{submit.error ? <ErrorAlert error={submit.error} until={submit.until} /> : null}
						<Form2>
							<Field id={ids.target} label={t.connect.target}>
								<NativeSelect id={ids.target} {...form.register("target")}>
									<option value="kb">{fill(t.connect.targetKb, { kb: kb.name })}</option>
									<option value="customer">{fill(t.connect.targetCustomer, { tenant: tenantName })}</option>
								</NativeSelect>
							</Field>
							<Field id={ids.install} label={t.connect.install}>
								<NativeSelect
									id={ids.install}
									value={install}
									onChange={(e) => onInstall(e.target.value as InstallMode)}
								>
									<option value="local">{t.connect.installLocal}</option>
									<option value="npx">{t.connect.installNpx}</option>
								</NativeSelect>
							</Field>
						</Form2>
						<Controller
							control={form.control}
							name="permission"
							render={({ field }) => <PermissionPicker value={field.value} onChange={field.onChange} />}
						/>
						<div className="flex flex-wrap items-center gap-3">
							<OwnerGate action="key.manage">
								<SubmitButton variant="default" pending={submit.isSubmitting} locked={submit.locked}>
									{t.connect.submit}
								</SubmitButton>
							</OwnerGate>
							{!owner ? (
								<small className="inline-flex items-center gap-1.5 text-[12.5px] text-muted-foreground">
									<Lock className="size-3.5" aria-hidden />
									{t.connect.memberNote}
								</small>
							) : null}
						</div>
					</form>
				)}
			</div>
		</Panel>
	);
}
