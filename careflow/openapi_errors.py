"""Declare the fixed error responses in the OpenAPI document, so the frontend can generate types for them (docs/design/frontend-contract.md).

The app already answers every refusal with a FIXED body (`{"detail": "<fixed words>"}`, and for a 422 a list of `{type, loc, msg}` with no echoed input), but FastAPI only declares the 422 it
knows about, in its own shape. This module post-processes the generated document: every operation under /v1 declares 401 (authentication comes first on every route), and every other status that
the handler's docstring names as one it answers (403, 404, 409, 422, 503), each with the right body schema. The docstrings ARE the source: a status written there is declared, and
`tests/test_openapi_errors.py` fails if a route's docstring lists a status the document does not declare. Nothing here changes behaviour; it only describes it.
"""

import re
from collections.abc import Callable, Iterable
from typing import Any, Final

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

DECLARABLE: Final = (401, 403, 404, 409, 422, 429, 503)
DESCRIPTIONS: Final = {
    401: "Missing, invalid or expired credentials (or, on the merge routes, a second factor is required). Fixed body.",
    403: "Authenticated, but this role may not do this. Fixed body.",
    404: "Not found. Also returned for a record in another clinic: invisible and missing are one answer. Fixed body.",
    409: "Conflict: the idempotency key was used for a different request, or the record is in a state that does not allow this. Fixed body.",
    422: "Invalid request: a list of {type, loc, msg}; the offending input is never echoed.",
    429: "Too many requests: the per-user limit was reached; wait and try again. Fixed body.",
    503: "Unavailable: the feature is not configured in this deployment, or a dependency is down. Fixed body.",
}
_STATUS = re.compile(r"(?<![0-9A-Za-z.])(401|403|404|409|422|429|503)(?![0-9A-Za-z])")


def statuses_in(doc: str | None) -> set[int]:
    """Every declarable status the docstring mentions."""
    return {int(m) for m in _STATUS.findall(doc or "")}


def _schemas() -> dict[str, Any]:
    return {
        "ErrorBody": {"type": "object", "title": "ErrorBody", "required": ["detail"], "properties": {"detail": {"type": "string", "title": "Detail"}}, "additionalProperties": False},
        "ValidationErrorBody": {
            "type": "object", "title": "ValidationErrorBody", "required": ["detail"], "additionalProperties": False,
            "properties": {
                "detail": {
                    "type": "array", "title": "Detail",
                    "items": {
                        "type": "object", "required": ["type", "loc", "msg"], "additionalProperties": False,
                        "properties": {
                            "type": {"type": "string"}, "loc": {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]}}, "msg": {"type": "string"},
                        },
                    },
                }
            },
        },
    }


def _response(status: int) -> dict[str, Any]:
    ref = "ValidationErrorBody" if status == 422 else "ErrorBody"
    return {"description": DESCRIPTIONS[status], "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{ref}"}}}}


def install(app: FastAPI, routers: Iterable[APIRouter]) -> None:
    """Replace `app.openapi` with a version that declares the error responses (cached like FastAPI's own). The handlers are found through the routers the app includes (FastAPI wraps an
    included router, so `app.routes` no longer lists its operations)."""
    original: Callable[[], dict[str, Any]] = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = original()
        by_path_method: dict[tuple[str, str], APIRoute] = {}
        for router in routers:
            for route in router.routes:
                if isinstance(route, APIRoute) and route.path.startswith("/v1"):
                    for method in route.methods:
                        by_path_method[(route.path, method.lower())] = route
        for path, operations in schema["paths"].items():
            for method, operation in operations.items():
                route = by_path_method.get((path, method))
                if route is None:
                    continue
                wanted = statuses_in(route.endpoint.__doc__) | {401} | ({422} if "422" in operation.get("responses", {}) else set())  # FastAPI's own 422 is replaced by the app's real shape
                for status in sorted(wanted):
                    operation.setdefault("responses", {})[str(status)] = _response(status)  # replaces FastAPI's own 422 shape
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        components.update(_schemas())
        # FastAPI's own validation-error schemas are no longer referenced by any /v1 operation; leave them out of the document so there is one 422 shape.
        for unused in ("HTTPValidationError", "ValidationError"):
            if not _referenced(schema, unused):
                components.pop(unused, None)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def _referenced(schema: dict[str, Any], name: str) -> bool:
    needle = f"#/components/schemas/{name}"

    def walk(node: Any) -> bool:
        if isinstance(node, dict):
            return node.get("$ref") == needle or any(walk(v) for v in node.values())
        if isinstance(node, list):
            return any(walk(v) for v in node)
        return False

    return walk({k: v for k, v in schema.items() if k != "components"}) or walk({k: v for k, v in schema.get("components", {}).get("schemas", {}).items() if k != name})
