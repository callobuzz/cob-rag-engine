import type { Server } from "node:http";

export const PORT: number;
export const WIDGET_STUB: string;
/** Serves snapshot folders; `/_landing/...` comes from `assets` (or whichever snapshot has the file). */
export function startServer(root: string, assets?: string, port?: number): Promise<Server>;
