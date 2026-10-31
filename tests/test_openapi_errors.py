"""The fixed error responses are DECLARED in the OpenAPI document (careflow/openapi_errors.py). Offline: reads the live app's schema."""

import pytest

from careflow import app as app_module
from careflow.app import create_app
from careflow.openapi_errors import DECLARABLE, statuses_in

ROUTERS = [
    app_module.router, app_module.scheduling, app_module.staff_messages, app_module.staff_patients, app_module.staff_merges, app_module.staff_referrals,
    app_module.staff_review_tasks, app_module.staff_documents, app_module.staff_identity, app_module.staff_audit,
]
SCHEMA = create_app().openapi()


def v1_operations():
    for path, operations in SCHEMA["paths"].items():
        if path.startswith("/v1"):
            for method, operation in operations.items():
                yield path, method, operation


def handlers():
    for router in ROUTERS:
        for route in router.routes:
            if route.path.startswith("/v1"):
                for method in route.methods:
                    yield route.path, method.lower(), route.endpoint


def test_every_v1_route_declares_401_because_authentication_comes_first():
    missing = [(p, m) for p, m, op in v1_operations() if "401" not in op["responses"]]
    assert not missing, missing


def test_every_status_a_docstring_lists_is_declared_with_the_right_body():
    declared = {(p, m): op["responses"] for p, m, op in v1_operations()}
    problems = []
    for path, method, endpoint in handlers():
        for status in statuses_in(endpoint.__doc__):
            response = declared[(path, method)].get(str(status))
            ref = response["content"]["application/json"]["schema"]["$ref"] if response else None
            if ref != f"#/components/schemas/{'ValidationErrorBody' if status == 422 else 'ErrorBody'}":
                problems.append((method.upper(), path, status, ref))
    assert not problems, problems


def test_there_is_one_422_shape_and_it_is_the_apps_not_fastapis():
    text = str(SCHEMA)
    assert "HTTPValidationError" not in text and "'ValidationError'" not in text
    assert set(SCHEMA["components"]["schemas"]["ErrorBody"]["required"]) == {"detail"}
    item = SCHEMA["components"]["schemas"]["ValidationErrorBody"]["properties"]["detail"]["items"]
    assert set(item["required"]) == {"type", "loc", "msg"} and "input" not in item["properties"]  # the offending input is never echoed


def test_the_health_route_outside_v1_is_untouched():
    assert set(SCHEMA["paths"]["/readyz"]["get"]["responses"]) == {"200"}


@pytest.mark.parametrize(
    ("doc", "expected"),
    [
        ("404 if you cannot see it, 403 if you may not, 409 if taken, 422 for a bad id, 503 if not configured.", {404, 403, 409, 422, 503}),
        ("401 step-up required, 404 unknown clinic/patient", {401, 404}),
        ("limit 500, version 4040, v1.403 and 2403", set()),
        (None, set()),
    ],
)
def test_the_docstring_reader_takes_whole_status_numbers_only(doc, expected):
    assert statuses_in(doc) == expected
    assert DECLARABLE == (401, 403, 404, 409, 422, 429, 503)
