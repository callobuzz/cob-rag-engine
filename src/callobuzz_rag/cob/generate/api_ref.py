"""The API reference: one Markdown page per OpenAPI tag, written from `docs/openapi.json`.

Each operation is a `## METHOD path` section with who can call it, its parameters, request
body fields, response codes and a curl example. The curl examples use the literal `{{BASE_URL}}`
(the in-app Docs page swaps in the reader's own engine address) and `$COB_RAG_KEY`.
Operations under `/api/v1/admin/`, and every other operation only the admin can call (found
from the app's routes, not the path), carry an `<!-- audience: admin -->` marker.
"""

import json
from pathlib import Path
from typing import Any

from callobuzz_rag.api.permissions import ROUTE_OPS, needed_permission
from callobuzz_rag.cob.sources.common import slugify

OUT_DIR = Path("docs") / "cob" / "generated" / "api"
OPENAPI = Path("docs") / "openapi.json"
_METHODS = ("get", "post", "put", "patch", "delete")
_PORTAL_NOTE = (
    "Only available when the Content Portal is enabled (`RAG_ENABLE_CONTENT_PORTAL=true`)."
)

Spec = dict[str, Any]
AdminOnly = frozenset[tuple[str, str]]
ADMIN_PREFIX = "/api/v1/admin/"


def _needs_admin(dependant: Any) -> bool:
    from callobuzz_rag.api.deps import require_admin

    return any(sub.call is require_admin or _needs_admin(sub) for sub in dependant.dependencies)


def _walk(routes: Any) -> Any:
    """The API routes under `routes`, with their full path, methods and dependencies.

    FastAPI 0.14x keeps an included router as a lazy node whose routes are built on demand
    (`effective_candidates`); older versions flatten them into `app.routes`. Both shapes carry
    `path`, `methods` and `dependant` on the route entries."""
    for route in routes:
        nested = getattr(route, "effective_candidates", None)
        if callable(nested):
            yield from _walk(nested())
        elif getattr(route, "dependant", None) is not None and getattr(route, "methods", None):
            yield route


def admin_only_routes() -> AdminOnly:
    """(METHOD, path) of every route that depends on `require_admin`, whatever its path."""
    import os
    from unittest.mock import patch

    from callobuzz_rag.api.app import create_app
    from callobuzz_rag.core.config import Settings

    def _defaults() -> Settings:
        return Settings(_env_file=None, enable_content_portal=True)

    env = {k: v for k, v in os.environ.items() if not k.startswith("RAG_")}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("callobuzz_rag.api.app.get_settings", _defaults),
        patch("callobuzz_rag.core.config.get_settings", _defaults),
    ):
        app = create_app()
    return frozenset(
        (method, route.path)
        for route in _walk(app.routes)
        if _needs_admin(route.dependant)
        for method in route.methods
    )


def is_admin_only(method: str, path: str, admin_only: AdminOnly) -> bool:
    return path.startswith(ADMIN_PREFIX) or (method.upper(), path) in admin_only


def _resolve(schema: Spec, spec: Spec) -> Spec:
    ref = schema.get("$ref")
    if ref:
        node: Any = spec
        for part in ref.removeprefix("#/").split("/"):
            node = node[part]
        return {**node, "_name": ref.rsplit("/", 1)[-1]}
    return schema


def _type(schema: Spec, spec: Spec) -> str:
    schema = _resolve(schema, spec)
    if "enum" in schema:
        return " | ".join(f'"{v}"' if isinstance(v, str) else str(v) for v in schema["enum"])
    if "const" in schema:
        return f'"{schema["const"]}"'
    if "anyOf" in schema or "oneOf" in schema:
        options = schema.get("anyOf") or schema.get("oneOf") or []
        names = [_type(o, spec) for o in options if o.get("type") != "null"]
        text = " or ".join(dict.fromkeys(names))
        return text + (" or null" if any(o.get("type") == "null" for o in options) else "")
    kind = schema.get("type")
    if kind == "array":
        return f"list of {_type(schema.get('items', {}), spec)}"
    if kind == "object" or "properties" in schema:
        return str(schema.get("_name", "object"))
    if kind == "string" and schema.get("format"):
        return f"string ({schema['format']})"
    return str(kind or schema.get("_name", "any"))


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _plain(text: str) -> str:
    """Description text safe inside a page: no line may start a heading."""
    lines = [("\\" + ln if ln.lstrip().startswith("#") else ln) for ln in text.strip().splitlines()]
    return "\n".join(lines)


def _auth(op: Spec, method: str, path: str, admin_only: AdminOnly) -> str:
    security = op.get("security")
    names = {n for entry in (security or []) for n in entry}
    if not security:
        return "No credential."
    if names == {"WidgetKey"}:
        return "A publishable widget key (`X-Widget-Key`)."
    if names == {"BearerAuth"}:
        return "A dashboard sign-in only (an API key is refused)."
    if is_admin_only(method, path, admin_only):
        return "The admin key, or the platform operator's dashboard sign-in."
    operation = ROUTE_OPS.get((method.upper(), path))
    if operation is None:
        return "An API key or a dashboard sign-in."
    permission = needed_permission(operation)
    return f"An API key with `{permission}` permission, or a dashboard sign-in."


def _sample(schema: Spec, spec: Spec) -> Any:
    schema = _resolve(schema, spec)
    if "enum" in schema:
        return schema["enum"][0]
    if "default" in schema and schema["default"] is not None:
        return schema["default"]
    if "anyOf" in schema:
        for option in schema["anyOf"]:
            if option.get("type") != "null":
                return _sample(option, spec)
    kind = schema.get("type")
    if kind == "string":
        fmt = schema.get("format")
        return {
            "uuid": "<uuid>",
            "date-time": "2026-01-01T00:00:00Z",
            "email": "you@example.com",
        }.get(str(fmt), "example")
    if kind == "integer":
        return schema.get("minimum", 1)
    if kind == "number":
        return schema.get("minimum", 1.0)
    if kind == "boolean":
        return True
    if kind == "array":
        return []
    return {}


def _body_section(op: Spec, spec: Spec) -> tuple[list[str], str | None, str | None]:
    """(markdown lines, curl data flag text, content type)."""
    body = op.get("requestBody")
    if not body:
        return [], None, None
    content = body.get("content", {})
    media = "application/json" if "application/json" in content else next(iter(content), "")
    schema = _resolve(content.get(media, {}).get("schema", {}), spec)
    props: Spec = schema.get("properties", {})
    required = set(schema.get("required", []))
    lines = [
        f"**Request body** (`{media}`{', ' + schema['_name'] if '_name' in schema else ''})",
        "",
    ]
    if props:
        lines += ["| Field | Type | Required | Description |", "|---|---|---|---|"]
        for name, prop in props.items():
            resolved = _resolve(prop, spec)
            desc = prop.get("description") or resolved.get("description") or ""
            if "default" in prop and prop["default"] is not None:
                stop = "" if not desc or desc.rstrip().endswith((".", "!", "?")) else "."
                desc = (desc.rstrip() + stop + f" Default `{json.dumps(prop['default'])}`.").strip()
            req = "yes" if name in required else "no"
            lines.append(f"| `{name}` | {_cell(_type(prop, spec))} | {req} | {_cell(desc)} |")
    else:
        lines.append("A JSON object." if "json" in media else "")
    lines.append("")
    sample = {n: _sample(props[n], spec) for n in props if n in required}
    if "multipart" in media:
        flags = " ".join(
            f'-F "{n}={"@document.pdf" if props[n].get("format") == "binary" else _flag(v)}"'
            for n, v in sample.items()
        )
        return lines, flags, media
    return lines, "-d '" + json.dumps(sample, ensure_ascii=False) + "'", media


def _flag(value: Any) -> str:
    return json.dumps(value) if not isinstance(value, str) else value


def _curl(method: str, path: str, op: Spec, data: str | None, media: str | None) -> str:
    url_path = path.replace("{", "<").replace("}", ">")
    queries = [
        f"{p['name']}=<{p['name']}>"
        for p in op.get("parameters", [])
        if p.get("in") == "query" and p.get("required")
    ]
    url = "{{BASE_URL}}" + url_path + ("?" + "&".join(queries) if queries else "")
    security = {n for entry in (op.get("security") or []) for n in entry}
    if security == {"WidgetKey"}:
        header = '-H "X-Widget-Key: $WIDGET_KEY"'
    elif security == {"BearerAuth"}:
        header = '-H "Authorization: Bearer $COB_RAG_TOKEN"'
    elif not security:
        header = ""
    else:
        header = '-H "X-API-Key: $COB_RAG_KEY"'
    parts = ["curl" if method.upper() == "GET" else f"curl -X {method.upper()}"]
    parts.append(f'"{url}"')
    if header:
        parts.append(header)
    if data and media and "json" in media:
        parts.append('-H "Content-Type: application/json"')
    if data:
        parts.append(data)
    return " \\\n  ".join(parts)


def _operation(method: str, path: str, op: Spec, spec: Spec, admin_only: AdminOnly) -> list[str]:
    lines = [f"## `{method.upper()} {path}`"]
    if is_admin_only(method, path, admin_only):
        lines.append("<!-- audience: admin -->")
    lines.append("")
    summary = op.get("summary", "")
    description = _plain(op.get("description", ""))
    lines.append(f"**{summary}.**" + (f" {description}" if description else ""))
    lines += ["", f"**Who can call it:** {_auth(op, method, path, admin_only)}", ""]
    params = [p for p in op.get("parameters", []) if p.get("in") in ("path", "query", "header")]
    if params:
        lines += [
            "**Parameters**",
            "",
            "| Name | In | Type | Required | Description |",
            "|---|---|---|---|---|",
        ]
        for p in params:
            desc = p.get("description") or ""
            lines.append(
                f"| `{p['name']}` | {p['in']} | {_cell(_type(p.get('schema', {}), spec))} | "
                f"{'yes' if p.get('required') else 'no'} | {_cell(desc)} |"
            )
        lines.append("")
    body_lines, data, media = _body_section(op, spec)
    lines += body_lines
    codes = ", ".join(
        f"`{code}` {_cell(r.get('description', ''))}" for code, r in op.get("responses", {}).items()
    )
    lines += [
        f"**Responses:** {codes}",
        "",
        "**Example**",
        "",
        "```bash",
        _curl(method, path, op, data, media),
        "```",
        "",
    ]
    return lines


_NAMES = {"cob": "COB"}


def _title(tag: str) -> str:
    return _NAMES.get(tag) or tag.replace("-", " ").capitalize()


def _lower(tag: str) -> str:
    return _NAMES.get(tag) or _title(tag).lower()


def render_api_pages(root: Path) -> dict[str, str]:
    spec: Spec = json.loads((root / OPENAPI).read_text(encoding="utf-8"))
    by_tag: dict[str, list[tuple[str, str, Spec]]] = {}
    for path, ops in spec["paths"].items():
        for method in _METHODS:
            op = ops.get(method)
            if op is None:
                continue
            tags = op.get("tags") or ["other"]
            by_tag.setdefault(tags[-1], []).append((method, path, op))
    admin_only = admin_only_routes()
    files: dict[str, str] = {}
    for tag in sorted(by_tag):
        title = f"{_title(tag)} API"
        out = [
            "---",
            f"title: {title}",
            "audience: customer",
            "topic: api",
            f"summary: Every {_lower(tag)} route of the engine API, with its parameters and a curl example.",
            "---",
            f"The **{_lower(tag)}** routes of the engine API. Replace `$COB_RAG_KEY` with an "
            "API key that has the permission shown under each route.",
            "",
        ]
        if tag.startswith("portal"):
            out += [_PORTAL_NOTE, ""]
        for method, path, op in by_tag[tag]:
            out += _operation(method, path, op, spec, admin_only)
        files[(OUT_DIR / f"{slugify(tag)}.md").as_posix()] = "\n".join(out).rstrip() + "\n"
    return files
