import { type ReactNode, useEffect, useId, useState } from "react";
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
import { Input } from "@/components/ui/input";
import { en } from "@/lib/strings/en";

/**
 * Typed confirmation: the button stays disabled until `value.trim() === word` (case-sensitive,
 * surrounding spaces forgiven, like the server). `error` shows on the field (e.g. the server's
 * `confirm_mismatch`); `children` adds fields after it (e.g. an optional reason). `blocked` keeps
 * the button off for a further condition the dialog's own children collect (an extra checkbox).
 */
export function TypedConfirmDialog({
	open,
	onOpenChange,
	title,
	body,
	word,
	confirmLabel,
	pending = false,
	blocked = false,
	error,
	children,
	onConfirm,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	title: string;
	body: ReactNode;
	word: string;
	confirmLabel: string;
	pending?: boolean;
	blocked?: boolean;
	error?: string | null;
	children?: ReactNode;
	onConfirm: (typed: string) => void;
}) {
	const [value, setValue] = useState("");
	const id = useId();
	useEffect(() => {
		if (open) setValue("");
	}, [open]);
	const matches = value.trim() === word;
	return (
		<AlertDialog open={open} onOpenChange={onOpenChange}>
			<AlertDialogContent>
				<AlertDialogHeader>
					<AlertDialogTitle>{title}</AlertDialogTitle>
					<AlertDialogDescription asChild>
						<div className="text-foreground-2">{body}</div>
					</AlertDialogDescription>
				</AlertDialogHeader>
				<div className="grid gap-1.5">
					<label htmlFor={id} className="text-[13px] font-semibold">
						Type <code>{word}</code> to confirm
					</label>
					<Input
						id={id}
						className="font-mono"
						autoComplete="off"
						value={value}
						onChange={(e) => setValue(e.target.value)}
						aria-invalid={error ? true : undefined}
						aria-describedby={error ? `${id}-error` : undefined}
					/>
					{error ? (
						<small id={`${id}-error`} className="text-[12.5px] font-medium text-destructive">
							{error}
						</small>
					) : null}
				</div>
				{children}
				<AlertDialogFooter>
					<AlertDialogCancel>{en.common.cancel}</AlertDialogCancel>
					<AlertDialogAction
						variant="destructive"
						disabled={!matches || pending || blocked}
						onClick={(e) => {
							e.preventDefault();
							if (matches && !blocked) onConfirm(value.trim());
						}}
					>
						{confirmLabel}
					</AlertDialogAction>
				</AlertDialogFooter>
			</AlertDialogContent>
		</AlertDialog>
	);
}
