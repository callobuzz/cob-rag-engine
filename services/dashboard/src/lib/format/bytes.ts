const units = ["B", "KB", "MB", "GB", "TB"];

export function formatBytes(n: number | null | undefined): string {
	if (n == null || Number.isNaN(n) || n < 0) return "—";
	let v = n;
	let i = 0;
	while (v >= 1024 && i < units.length - 1) {
		v /= 1024;
		i++;
	}
	return `${i === 0 ? v : v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}
