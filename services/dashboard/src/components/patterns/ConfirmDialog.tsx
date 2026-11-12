import type { ReactNode } from "react";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { en } from "@/lib/strings/en";

/** Plain confirmation (single-document delete, decline, reactivate…). Focus is trapped (Radix). */
export function ConfirmDialog({
	open,
	onOpenChange,
	title,
	body,
	confirmLabel,
	destructive = true,
	pending = false,
	children,
	onConfirm,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	title: string;
	body: ReactNode;
	confirmLabel: string;
	destructive?: boolean;
	pending?: boolean;
	/** Extra content after the body (e.g. an optional reason field or an error). */
	children?: ReactNode;
	onConfirm: () => void;
}) {
	return (
		<AlertDialog open={open} onOpenChange={onOpenChange}>
			<AlertDialogContent>
				<AlertDialogHeader>
					<AlertDialogTitle>{title}</AlertDialogTitle>
					<AlertDialogDescription asChild>
						<div className="text-foreground-2">{body}</div>
					</AlertDialogDescription>
				</AlertDialogHeader>
				{children}
				<AlertDialogFooter>
					<AlertDialogCancel>{en.common.cancel}</AlertDialogCancel>
					<AlertDialogAction
						variant={destructive ? "destructive" : "default"}
						disabled={pending}
						onClick={(e) => {
							e.preventDefault();
							onConfirm();
						}}
					>
						{confirmLabel}
					</AlertDialogAction>
				</AlertDialogFooter>
			</AlertDialogContent>
		</AlertDialog>
	);
}
