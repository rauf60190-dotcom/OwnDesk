"""The admin report must fail closed and never echo private configuration."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('public_auth_check',
    Path(__file__).resolve().parents[1] / 'infra' / 'public_auth_check.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

PROFILE = {'format': 1, 'public_hostname': 'owndesk.work', 'mail_enabled': True}
INTEGRATIONS = {
    'PUBLIC_REGISTRATION_ENABLED': '1', 'GOOGLE_CLIENT_ID': 'private-client',
    'GOOGLE_CLIENT_SECRET': 'private-google-secret', 'EMAIL_HOST': 'smtp.resend.com',
    'EMAIL_HOST_USER': 'resend', 'EMAIL_HOST_PASSWORD': 'private-mail-secret',
    'EMAIL_PORT': '587', 'EMAIL_USE_TLS': '1', 'EMAIL_USE_SSL': '0',
    'DEFAULT_FROM_EMAIL': 'OwnDesk <accounts@auth.owndesk.work>',
}


def write_config(path, profile=PROFILE, integrations=INTEGRATIONS):
    (path / 'runtime-profile.json').write_text(json.dumps(profile), encoding='utf-8')
    (path / 'integrations.json').write_text(json.dumps(integrations), encoding='utf-8')


def test_configured_does_not_mean_external_acceptance(tmp_path, capsys):
    write_config(tmp_path)
    assert checker.main(['--data-dir', str(tmp_path)]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report['email_configuration_ready']
    assert report['google_signup_configuration_ready']
    assert report['external_acceptance_performed'] is False
    assert 'private-' not in output
    assert 'accounts@' not in output


@pytest.mark.parametrize('change,code', [
    ({'EMAIL_HOST': ''}, 'smtp_host'),
    ({'EMAIL_HOST': 'https://secret@example.test'}, 'smtp_host'),
    ({'EMAIL_PORT': '0'}, 'smtp_port'),
    ({'EMAIL_PORT': '65536'}, 'smtp_port'),
    ({'EMAIL_PORT': 'not-a-port'}, 'smtp_port'),
    ({'EMAIL_PORT': '５８７'}, 'smtp_port'),
    ({'EMAIL_USE_TLS': '0', 'EMAIL_USE_SSL': '0'}, 'smtp_encryption'),
    ({'EMAIL_USE_TLS': '1', 'EMAIL_USE_SSL': '1'}, 'smtp_encryption'),
    ({'EMAIL_USE_TLS': 'true'}, 'smtp_encryption'),
    ({'EMAIL_HOST_PASSWORD': ''}, 'smtp_credentials'),
    ({'EMAIL_HOST_USER': ''}, 'smtp_credentials'),
    ({'DEFAULT_FROM_EMAIL': 'not-an-address'}, 'sender'),
    ({'DEFAULT_FROM_EMAIL': 'ok@example.test\r\nBcc: secret@example.test'}, 'sender'),
    ({'PUBLIC_REGISTRATION_ENABLED': '0'}, 'public_registration'),
])
def test_incomplete_or_unsafe_email_configuration_is_not_ready(change, code):
    report = checker.configuration_report(PROFILE, {**INTEGRATIONS, **change})
    assert not report['email_configuration_ready']
    assert not next(item for item in report['checks'] if item['code'] == code)['ok']


def test_google_and_email_are_independent():
    report = checker.configuration_report(PROFILE, {'GOOGLE_CLIENT_ID': 'id',
        'GOOGLE_CLIENT_SECRET': 'secret', 'PUBLIC_REGISTRATION_ENABLED': '1'})
    assert report['google_signup_configuration_ready']
    assert not report['email_configuration_ready']
    report = checker.configuration_report(PROFILE, {**INTEGRATIONS, 'GOOGLE_CLIENT_SECRET': ''})
    assert not report['google_signup_configuration_ready']
    assert report['email_configuration_ready']


@pytest.mark.parametrize('profile', [{**PROFILE, 'mail_enabled': False},
    {**PROFILE, 'mail_enabled': 'true'}, {**PROFILE, 'public_hostname': 'tenant.owndesk.work'}])
def test_wrong_profile_or_stopped_mail_worker_is_not_ready(profile):
    assert not checker.configuration_report(profile, INTEGRATIONS)['email_configuration_ready']


@pytest.mark.parametrize('raw', ['["private-secret"]', '{"GOOGLE_CLIENT_SECRET": ["private-secret"]}',
    '{"private-secret":', '"' + 'private-secret' * 6000 + '"'])
def test_bad_private_file_never_echoes_contents(tmp_path, capsys, raw):
    write_config(tmp_path)
    (tmp_path / 'integrations.json').write_text(raw, encoding='utf-8')
    assert checker.main(['--data-dir', str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert 'private-secret' not in output
    assert json.loads(output)['error'] == 'configuration_unreadable_or_invalid'


def test_missing_configuration_returns_safe_error(tmp_path, capsys):
    assert checker.main(['--data-dir', str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)['error'] == 'configuration_unreadable_or_invalid'


def test_check_never_modifies_configuration(tmp_path, capsys):
    write_config(tmp_path, integrations={})
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert checker.main(['--data-dir', str(tmp_path)]) == 2
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    assert not json.loads(capsys.readouterr().out)['email_configuration_ready']
