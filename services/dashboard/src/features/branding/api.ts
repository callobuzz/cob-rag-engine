import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";

/**
 * Sign-up mode and branding (A7): `GET/PATCH /api/v1/admin/settings`. A save invalidates the public
 * branding (`qk.branding`) so this dashboard's shell (sidebar name, colour, document title) updates
 * at once; other browsers follow within a minute (`Cache-Control: max-age=60`, engine cache 10 s).
 */
export type Settings = components["schemas"]["SettingsResponse"];
export type SettingsPatch = components["schemas"]["SettingsPatch"];
export type BrandingPatch = components["schemas"]["BrandingPatch"];
export type SignupMode = Settings["signup_mode"];

export const settingsKeys = { settings: ["admin", "settings"] as const };

export const settingsQuery = queryOptions({
	queryKey: settingsKeys.settings,
	queryFn: () => unwrap(api().GET("/api/v1/admin/settings")),
});

export function useSaveSettings() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: SettingsPatch) => unwrap(api().PATCH("/api/v1/admin/settings", { body })),
		onSuccess: (data) => {
			qc.setQueryData(settingsKeys.settings, data);
			return qc.invalidateQueries({ queryKey: qk.branding });
		},
	});
}
