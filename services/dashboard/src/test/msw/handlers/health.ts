import { HttpResponse, http } from "msw";
import { BASE } from "../data";

/** `GET /health` (public): every service the engine pings, all up. */
export const HEALTH_OK = {
	status: "ok",
	services: { api: true, postgres: true, redis: true, qdrant: true, storage: true },
};

export const healthHandlers = [http.get(`${BASE}/api/v1/health`, () => HttpResponse.json(HEALTH_OK))];
