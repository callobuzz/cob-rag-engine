// Zod 4 without its eval-compiled fast path, set before the app bundle runs (dashboard D12).
// Zod probes `new Function` when it builds an object schema; under the image's
// Content-Security-Policy (`script-src 'self'`, no 'unsafe-eval') that probe is blocked and
// reported as a violation. The flag is read when a schema is created, in whichever chunk that
// happens first, so it is set here, in a classic script, rather than in the bundle. Zod reads
// its configuration from this global (zod/v4/core/core.js).
globalThis.__zod_globalConfig = Object.assign(globalThis.__zod_globalConfig || {}, { jitless: true });
