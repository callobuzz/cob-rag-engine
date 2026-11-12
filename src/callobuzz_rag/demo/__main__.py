"""Command line of a demo server, run inside the app container.

    python -m callobuzz_rag.demo status
    python -m callobuzz_rag.demo reset [--wait]

Both talk to the running API on 127.0.0.1:8000 with the admin key from the environment (never
printed). Exit codes: 0 done, 1 failed, 2 demo mode is off.
"""

import argparse
import asyncio
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any, Final

import httpx

EXIT_OK: Final = 0
EXIT_FAILED: Final = 1
EXIT_OFF: Final = 2

DEFAULT_URL: Final = "http://127.0.0.1:8000"
POLL_S: Final = 5.0
WAIT_LIMIT_S: Final = 2400.0


def _out(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _describe(status: dict[str, Any]) -> list[str]:
    last = status.get("last_reset_at") or "never"
    result = status.get("last_result") or "-"
    step = status.get("last_failed_step")
    return [
        f"Demo mode: {'on' if status.get('enabled') else 'off'}",
        f"Reset running now: {'yes' if status.get('running') else 'no'}",
        f"Last good reset: {last} (last result: {result}{f', stopped at {step}' if step else ''})",
        f"Next nightly reset: {status.get('next_reset_at')}",
        f"Answers today: {status.get('answers_today')} of {status.get('answers_limit')} "
        f"(one visitor: {status.get('answers_per_visitor_limit')})",
        f"Model calls today: {status.get('model_calls_today')} of {status.get('model_calls_limit')}",
    ]


async def _get(http: httpx.AsyncClient) -> httpx.Response:
    return await http.get("/api/v1/admin/demo")


async def run(
    argv: Sequence[str],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], Any] = asyncio.sleep,
    admin_key: str | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m callobuzz_rag.demo")
    parser.add_argument("command", choices=["status", "reset"])
    parser.add_argument("--wait", action="store_true", help="with reset: wait until it is done")
    parser.add_argument("--engine-url", default=DEFAULT_URL, help=argparse.SUPPRESS)
    args = parser.parse_args(list(argv))

    if admin_key is None:
        from callobuzz_rag.core.config import get_settings

        admin_key = get_settings().admin_api_key.get_secret_value()
    if not admin_key:
        _out("RAG_ADMIN_API_KEY is not set in this environment.")
        return EXIT_FAILED

    async with httpx.AsyncClient(
        base_url=args.engine_url,
        headers={"X-API-Key": admin_key},
        transport=transport,
        timeout=30.0,
    ) as http:
        try:
            if args.command == "status":
                reply = await _get(http)
                if reply.status_code == 404:
                    _out("Demo mode is off on this server.")
                    return EXIT_OFF
                if reply.status_code != 200:
                    _out(f"The server answered {reply.status_code}.")
                    return EXIT_FAILED
                for line in _describe(reply.json()):
                    _out(line)
                return EXIT_OK

            started = await http.post("/api/v1/admin/demo/reset")
            if started.status_code == 404:
                _out("Demo mode is off on this server.")
                return EXIT_OFF
            if started.status_code != 202:
                detail = started.json().get("detail") if started.content else ""
                _out(f"The reset did not start ({started.status_code}). {detail}")
                return EXIT_FAILED
            _out("The reset has started.")
            if not args.wait:
                return EXIT_OK
            begun = clock()
            while clock() - begun < WAIT_LIMIT_S:
                await sleep(POLL_S)
                reply = await _get(http)
                status = reply.json() if reply.status_code == 200 else {}
                if status and not status.get("running"):
                    if status.get("last_result") == "ok":
                        _out("The demo is back to the seed.")
                        return EXIT_OK
                    step = status.get("last_failed_step") or "unknown"
                    _out(f"The reset failed at the step: {step}.")
                    return EXIT_FAILED
            _out("Still running after 40 minutes; check the server log.")
            return EXIT_FAILED
        except httpx.HTTPError:
            _out(f"Could not reach the engine at {args.engine_url}. Is the server running?")
            return EXIT_FAILED


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
