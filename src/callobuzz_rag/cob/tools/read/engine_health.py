"""engine_health (operator): is each service up, the version, the models, the features on."""

from typing import Any, ClassVar

from callobuzz_rag.cob.tools.base import ReadTool, RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import esc, kv, link_to, table, ts
from callobuzz_rag.cob.tools.http import ToolFailure, fetch
from callobuzz_rag.cob.tools.registry import register
from callobuzz_rag.cob.types import CobRole
from callobuzz_rag.core.schemas import StrictRequest


class EngineHealthArgs(StrictRequest):
    pass


def _features_on(features: Any) -> list[str]:
    if not isinstance(features, dict):
        return []
    on = []
    for name, value in features.items():
        enabled = value.get("enabled") if isinstance(value, dict) else value
        if enabled is True:
            on.append(str(name).replace("_", " "))
    return sorted(on)


class EngineHealthTool(ReadTool):
    name: ClassVar[str] = "engine_health"
    description: ClassVar[str] = (
        "Operator only: whether the engine's services are up, its version, the models it "
        "uses and which features are on."
    )
    args_model: ClassVar[type[StrictRequest]] = EngineHealthArgs
    roles: ClassVar[frozenset[CobRole]] = frozenset({CobRole.ADMIN})
    routes: ClassVar[tuple[RouteCall, ...]] = (
        RouteCall("GET", "/api/v1/health/ready"),
        RouteCall("GET", "/api/v1/info"),
    )
    screen: ClassVar[str] = "/admin/engine"

    async def answer(self, ctx: ToolContext, args: Any) -> ToolResult:
        lang = ctx.language
        # The readiness probe answers 503 with its detail when a service is down.
        ready = await fetch(ctx.client, "/api/v1/health/ready", accept=(503,))
        services = (ready or {}).get("services")
        if not isinstance(services, dict):
            raise ToolFailure("unavailable")
        try:
            info = await fetch(ctx.client, "/api/v1/info") or {}
        except ToolFailure:
            info = {}
        down = sorted(name for name, up in services.items() if not up)
        sentence = (
            ts("health_down", lang, names=", ".join(esc(n, 30) for n in down))
            if down
            else ts("health_ok", lang)
        )
        rows = [[name, ts("up" if up else "down", lang)] for name, up in sorted(services.items())]
        text = f"{sentence}\n\n{table([ts('h_service', lang), ts('h_status', lang)], rows, language=lang)}"
        llm = info.get("llm") or {}
        embedding = info.get("embedding") or {}
        pairs: list[tuple[str, Any]] = []
        if info.get("version"):
            pairs.append((ts("kv_version", lang), info["version"]))
        if llm.get("model"):
            pairs.append((ts("kv_answer_model", lang), llm["model"]))
        if llm.get("fast_model"):
            pairs.append((ts("kv_fast_model", lang), llm["fast_model"]))
        if embedding.get("model"):
            pairs.append((ts("kv_embedding_model", lang), embedding["model"]))
        features = _features_on(info.get("features"))
        if features:
            pairs.append((ts("kv_features", lang), ", ".join(features)))
        if pairs:
            text += "\n\n" + kv(pairs)
        return ToolResult(
            markdown=text,
            link=link_to(self, ctx, self.screen),
            data={"down": down, "version": info.get("version")},
        )


register(EngineHealthTool())
