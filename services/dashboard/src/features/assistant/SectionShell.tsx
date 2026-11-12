import type { ReactNode } from "react";
import { type FieldValues, FormProvider, type UseFormReturn } from "react-hook-form";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Button } from "@/components/ui/button";
import type { ApiError } from "@/lib/api/errors";
import type { useAppSubmit } from "@/lib/forms/app-form";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { KbErrorAlert } from "../kbs/KbErrorAlert";
import { FormFoot } from "../kbs/ui";
/** What `useDraftSection` returns, as far as the frame needs it. */
export interface SectionLike<V extends FieldValues> {
	form: UseFormReturn<V>;
	submit: ReturnType<typeof useAppSubmit<V>>;
	guard: ReactNode;
	conflict: ApiError | null;
	reloadTheirs: () => void;
}

/**
 * The frame every section form shares: the form element (react-hook-form context for the
 * controls), the "someone else saved" and error alerts, the Discard / Save footer and the
 * unsaved-changes guard.
 */
export function SectionShell<V extends FieldValues>({
	section,
	children,
	notice,
}: {
	section: SectionLike<V>;
	children: ReactNode;
	/** Extra alert content above the fields (e.g. a warning about this section). */
	notice?: ReactNode;
}) {
	const { form, submit, guard, conflict, reloadTheirs } = section;
	return (
		<FormProvider {...form}>
			<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
				{conflict ? (
					<FormAlert>
						<b className="font-semibold">{t.form.conflictTitle}</b>
						<span className="mt-1 block">
							<Button type="button" variant="outline" size="sm" onClick={reloadTheirs}>
								{t.form.conflictLoad}
							</Button>
						</span>
					</FormAlert>
				) : null}
				{submit.error ? <KbErrorAlert error={submit.error} /> : null}
				{notice}
				{children}
				<div className="sticky bottom-0 z-10 overflow-hidden rounded-lg border border-border">
					<FormFoot>
						<span className="mr-auto self-center text-[12.5px] text-muted-foreground">{t.form.onlyAfterPublish}</span>
						<Button type="button" variant="ghost" disabled={!form.formState.isDirty} onClick={() => form.reset()}>
							{t.form.discard}
						</Button>
						<SubmitButton
							variant="default"
							pending={submit.isSubmitting}
							locked={submit.locked}
							pendingLabel={t.form.saving}
						>
							{t.form.save}
						</SubmitButton>
					</FormFoot>
				</div>
				{guard}
			</form>
		</FormProvider>
	);
}
