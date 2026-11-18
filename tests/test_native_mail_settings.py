import importlib.util
import json
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("native_mail_runtime", Path(__file__).resolve().parents[1] / "infra/native_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def test_private_smtp_configuration_reaches_organization_worker(tmp_path):
    mail = {"EMAIL_HOST":"smtp.example.test", "EMAIL_HOST_PASSWORD":"test-only", "EMAIL_PORT":"587", "EMAIL_USE_TLS":"1", "DEFAULT_FROM_EMAIL":"sender@example.test"}
    (tmp_path / "mail-settings.json").write_text(json.dumps(mail))
    # External database configuration is mandatory, but no connection is made here.
    import os
    from unittest.mock import patch
    with patch.dict(os.environ, {"DATABASE_URL":"postgresql://test:test@localhost/test"}):
        env = runtime.environment(tmp_path, {"public_hostname":"demo.example.test"},
            {"SECRET_KEY":"test", "TOKEN_ENCRYPTION_KEY":"test", "ACCOUNT_ENCRYPTION_KEY":"test"})
    assert all(env[key] == value for key, value in mail.items())
    assert env["PUBLIC_REGISTRATION_ENABLED"] == "0"


@pytest.mark.parametrize("mail", [{"GOOGLE_CLIENT_SECRET":"wrong-scope"}, {"EMAIL_PORT":587},
    {"EMAIL_HOST":"smtp.example.test", "EMAIL_USE_TLS":"0", "EMAIL_USE_SSL":"0"}])
def test_private_mail_file_rejects_wrong_scope_and_unencrypted_delivery(tmp_path, mail):
    (tmp_path / "mail-settings.json").write_text(json.dumps(mail))
    with pytest.raises(ValueError):
        runtime.environment(tmp_path, {"public_hostname":"demo.example.test"},
            {"SECRET_KEY":"test", "TOKEN_ENCRYPTION_KEY":"test", "ACCOUNT_ENCRYPTION_KEY":"test"})
