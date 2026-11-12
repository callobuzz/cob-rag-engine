"""Startup audit of request-body strictness.

Pydantic's default is ``extra='ignore'``: a field the model does not declare is
dropped, silently, and the request succeeds. That is how a client sent
``search_mode: 'hybrid'`` for months against an engine that never had the field
— the "hybrid fallback" re-ran the identical dense query and reported success at
every layer.

Every request body now inherits `core.schemas.StrictRequest` (``extra='forbid'``), so an
unknown field is a 422 naming it. This module keeps that true: it checks, on every boot,
that no request body (nor a model nested inside one) can swallow a field, and a unit test
fails if one can. A warning nobody reads is what let the original bug live, so a
permissive model is logged at WARNING and named, not a single line that scrolls past.
"""

import inspect
import logging
from collections.abc import Callable, Iterable, Iterator
from typing import TYPE_CHECKING, Any, get_args

from pydantic import BaseModel

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)


def _walk(routes: Iterable[Any], seen: set[int]) -> Iterator[Callable[..., Any]]:
    for route in routes:
        if id(route) in seen:
            continue
        seen.add(id(route))
        endpoint = getattr(route, "endpoint", None)
        if endpoint is not None:
            yield endpoint
            continue
        # FastAPI >= 0.14x keeps `include_router` as a wrapper holding the original
        # router (`original_router`), nested as deep as the includes go. A Starlette
        # `Mount` exposes its app's routes as `routes`.
        router = getattr(route, "original_router", None)
        children = getattr(router, "routes", None) if router is not None else None
        if children is None:
            children = getattr(route, "routes", None)
        if children:
            yield from _walk(children, seen)


def iter_endpoints(app: "FastAPI") -> Iterator[Callable[..., Any]]:
    """Every route handler in the app, including those of included and nested routers."""
    return _walk(app.routes, set())


def _resolve(annotation: Any, endpoint: Callable[..., Any]) -> Any:
    """The annotation itself, or, for a string one, what it names in the endpoint's module.

    A module with `from __future__ import annotations` (routes/query.py) holds its
    annotations as strings, which never match a model class. Each is looked up on its
    own: a name imported only under TYPE_CHECKING (``AsyncSession``) is not there, and
    must not stop the body model next to it from being found. A body is annotated with
    a bare class name, so a plain lookup is enough; nothing is evaluated.
    """
    if not isinstance(annotation, str):
        return annotation
    return getattr(endpoint, "__globals__", {}).get(annotation.strip())


def _request_body_models(app: "FastAPI") -> dict[str, type[BaseModel]]:
    """Every pydantic model that appears as a route handler parameter.

    Keyed by class name, so a model reused across routes is reported once. A
    parameter filled by `Depends(...)` (the engine `Settings`, say) is injected,
    not sent by the caller, so it is not a request body.
    """
    from fastapi import params

    models: dict[str, type[BaseModel]] = {}
    for endpoint in iter_endpoints(app):
        try:
            signature = inspect.signature(endpoint)
        except (ValueError, TypeError):
            continue
        for parameter in signature.parameters.values():
            annotation = _resolve(parameter.annotation, endpoint)
            if (
                not isinstance(parameter.default, params.Depends)
                and isinstance(annotation, type)
                and issubclass(annotation, BaseModel)
                and annotation is not BaseModel
            ):
                models[annotation.__name__] = annotation
    for model in list(models.values()):
        _add_nested(model, models)
    return models


def _add_nested(model: type[BaseModel], models: dict[str, type[BaseModel]]) -> None:
    """Add the models a body's fields hold (a list of records, a config object), at any depth:
    they are part of the body, so an unknown field inside one is dropped just the same."""
    pending: list[Any] = [f.annotation for f in model.model_fields.values()]
    while pending:
        annotation = pending.pop()
        pending.extend(get_args(annotation))
        if (
            isinstance(annotation, type)
            and issubclass(annotation, BaseModel)
            and annotation is not BaseModel
            and annotation.__name__ not in models
        ):
            models[annotation.__name__] = annotation
            _add_nested(annotation, models)


def audit_request_schema_strictness(app: "FastAPI") -> list[str]:
    """Log which request bodies still silently drop unknown fields.

    Returns the permissive model names so a test can assert on them rather than
    scraping log output.
    """
    models = _request_body_models(app)
    permissive = sorted(
        name for name, model in models.items() if model.model_config.get("extra") != "forbid"
    )
    strict = sorted(set(models) - set(permissive))

    if strict:
        logger.info(
            "Request schemas rejecting unknown fields (%d): %s",
            len(strict),
            ", ".join(strict),
        )

    if permissive:
        logger.warning(
            "%d request schema(s) IGNORE unknown fields — a caller sending "
            "a field these do not declare gets a silent no-op, not a 422: %s",
            len(permissive),
            ", ".join(permissive),
        )

    return permissive
