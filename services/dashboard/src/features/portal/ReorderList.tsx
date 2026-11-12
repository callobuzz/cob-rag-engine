import { type ReactNode, useState } from "react";
import { fill } from "@/lib/strings/en";
import { portalStrings as t } from "@/lib/strings/portal";
import { cn } from "@/lib/utils";

/** `ids` with the item at `from` moved to `to` (both clamped); the same array when nothing moves. */
export function moveItem<T>(ids: readonly T[], from: number, to: number): T[] {
	const target = Math.max(0, Math.min(ids.length - 1, to));
	if (from < 0 || from >= ids.length || from === target) return [...ids];
	const next = [...ids];
	const [item] = next.splice(from, 1);
	if (item !== undefined) next.splice(target, 0, item);
	return next;
}

export interface ReorderItemApi {
	/** Move up / down by one (keyboard buttons); undefined at the ends or when reordering is off. */
	up?: () => void;
	down?: () => void;
	/** The drag handle's `onDragStart`; undefined when reordering is off. */
	dragStart?: (e: React.DragEvent) => void;
}

/**
 * The chunks in order. Reordering works two ways: "Move up/down" buttons on each card (keyboard)
 * and dragging a card's handle onto another card. Either sends the whole new order.
 */
export function ReorderList<T extends { id: string }>({
	items,
	enabled,
	label,
	onReorder,
	render,
}: {
	items: T[];
	enabled: boolean;
	label: string;
	onReorder: (ids: string[], movedTo: number) => void;
	render: (item: T, index: number, api: ReorderItemApi) => ReactNode;
}) {
	const [dragging, setDragging] = useState<number | null>(null);
	const [over, setOver] = useState<number | null>(null);
	const ids = items.map((i) => i.id);
	const move = (from: number, to: number) => {
		if (from === to) return;
		onReorder(moveItem(ids, from, to), to + 1);
	};
	return (
		<ol aria-label={label} className="m-0 grid list-none gap-3 p-0">
			{items.map((item, i) => (
				<li
					key={item.id}
					onDragOver={
						enabled && dragging !== null
							? (e) => {
									e.preventDefault();
									setOver(i);
								}
							: undefined
					}
					onDragEnd={() => {
						setDragging(null);
						setOver(null);
					}}
					onDragLeave={() => setOver((o) => (o === i ? null : o))}
					onDrop={
						enabled && dragging !== null
							? (e) => {
									e.preventDefault();
									const from = dragging;
									setDragging(null);
									setOver(null);
									move(from, i);
								}
							: undefined
					}
					className={cn("rounded-lg", over === i && dragging !== i && "outline-2 outline-primary outline-dashed")}
				>
					{render(item, i, {
						up: enabled && i > 0 ? () => move(i, i - 1) : undefined,
						down: enabled && i < items.length - 1 ? () => move(i, i + 1) : undefined,
						dragStart: enabled
							? (e) => {
									e.dataTransfer.effectAllowed = "move";
									e.dataTransfer.setData("text/plain", fill(t.chunks.chunk, { n: i + 1 }));
									setDragging(i);
								}
							: undefined,
					})}
				</li>
			))}
		</ol>
	);
}
