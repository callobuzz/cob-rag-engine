import { toast } from "sonner";
import { TypedConfirmDialog } from "@/components/patterns/TypedConfirmDialog";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { fill } from "@/lib/strings/en";
import { leadsStrings as t } from "@/lib/strings/leads";
import { useDeleteLead } from "./api";

/** Erases the lead and the website chat it came from; the consent record stays, without contact data. */
export function DeleteLeadDialog({
	id,
	who,
	open,
	onOpenChange,
	onDeleted,
}: {
	id: string;
	who: string;
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onDeleted: () => void;
}) {
	const remove = useDeleteLead();
	return (
		<TypedConfirmDialog
			open={open}
			onOpenChange={onOpenChange}
			title={t.delete.title}
			body={fill(t.delete.body, { who })}
			word={t.delete.word}
			confirmLabel={t.delete.confirm}
			pending={remove.isPending}
			error={remove.isError ? messageFor(toApiError(remove.error)).title : null}
			onConfirm={() =>
				remove.mutate(id, {
					onSuccess: () => {
						toast.success(t.delete.done);
						onOpenChange(false);
						onDeleted();
					},
				})
			}
		/>
	);
}
