"""GET /v1/iam/me carries `operating_profile`: the deployment's CAREFLOW_PROFILE restricted to the two known values (demo, clinical); anything else is null. Offline."""

import pytest

from careflow.app import Me, operating_profile


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("demo", "demo"), ("clinical", "clinical"), (" Demo ", "demo"), ("CLINICAL", "clinical"), (None, None), ("", None), ("   ", None), ("staging", None), ("production", None), ("demo;clinical", None), ("dem0", None)],
)
def test_the_profile_is_one_of_the_two_known_values_or_null(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("CAREFLOW_PROFILE", raising=False)
    else:
        monkeypatch.setenv("CAREFLOW_PROFILE", raw)
    assert operating_profile() == expected


def test_the_response_model_only_admits_the_known_values_and_null():
    base = {"account_id": "00000000-0000-0000-0000-000000000001", "account_type": "staff", "status": "active", "roles": []}
    for good in ("demo", "clinical", None):
        assert Me(**base, operating_profile=good).operating_profile == good
    with pytest.raises(ValueError):
        Me(**base, operating_profile="staging")  # type: ignore[arg-type]
    assert "operating_profile" in Me.model_fields and Me.model_fields["operating_profile"].is_required()


def test_it_names_the_profile_only_never_another_setting():
    import inspect

    from careflow import app as app_module

    source = inspect.getsource(app_module.operating_profile)
    assert source.count("os.environ.get") == 1 and "CAREFLOW_PROFILE" in source  # reads exactly one variable
