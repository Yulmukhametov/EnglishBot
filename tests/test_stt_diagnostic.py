import importlib.util
import io
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "stt_diagnostic", Path(__file__).parents[1] / "deploy/check_hermes_stt.py"
)
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


def test_error_detail_redacts_credentials_and_omits_other_fields():
    payload = {"detail": {"status": "invalid_request", "message": "key secret-value sk_other-key",
                          "headers": {"xi-api-key": "another-secret"}}}
    result = diagnostic.error_detail(io.BytesIO(json.dumps(payload).encode()), "secret-value")
    assert "invalid_request" in result
    assert "secret-value" not in result
    assert "sk_other-key" not in result
    assert "another-secret" not in result


def test_error_detail_never_prints_non_json_response():
    result = diagnostic.error_detail(io.BytesIO(b"<html>private response</html>"), "secret")
    assert "private response" not in result


def test_error_detail_limits_output():
    payload = {"detail": "x" * 4000}
    assert len(diagnostic.error_detail(io.BytesIO(json.dumps(payload).encode()), "secret")) <= 1200
