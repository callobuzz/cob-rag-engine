import { z } from "zod";

/**
 * Zod without its eval-compiled fast path. Zod 4 probes `new Function` when an object schema is
 * built; under the image's Content-Security-Policy (`script-src 'self'`, no 'unsafe-eval') that
 * probe is blocked and reported as a violation. jitless is read when a schema is created, so
 * main.tsx imports this module first, before any module that builds a schema.
 */
z.config({ jitless: true });
