import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

INFRA = Path(__file__).resolve().parents[1] / 'infra'
sys.path.insert(0, str(INFRA))
spec = importlib.util.spec_from_file_location('portal_runtime', INFRA / 'portal_runtime.py')
portal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(portal)


def profile():
    return {'format': 1, 'public_hostname': 'owndesk.work', 'api_port': 15186, 'origin_port': 15185,
            'postgres': {'bin': 'runtime/postgresql/bin', 'port': 15585, 'database': 'portal',
                         'role': 'portal_app', 'admin_role': 'portal_dba'}}


@pytest.mark.parametrize('change', [
    {'public_hostname': 'another.example'}, {'api_port': 15185}, {'api_port': True},
    {'protocol': 'invalid'}, {'mail_enabled': 'false'},
])
def test_invalid_portal_profile_is_rejected(tmp_path, change):
    (tmp_path / 'runtime-profile.json').write_text(json.dumps({**profile(), **change}))
    with pytest.raises(ValueError):
        portal.profile_for(tmp_path)


def test_portal_ignores_inherited_credentials_and_preserves_keys(tmp_path):
    secret = dict(SECRET_KEY='original-session-key', ACCOUNT_ENCRYPTION_KEY='original-account-key',
                  POSTGRES_ADMIN_PASSWORD='admin', POSTGRES_APP_PASSWORD='pass:@/?')
    with patch.dict(portal.os.environ, {'CLOUDFLARE_API_TOKEN': 'foreign', 'GOOGLE_CLIENT_SECRET': 'foreign',
                                      'TOKEN_ENCRYPTION_KEY': 'tenant', 'TUNNEL_TOKEN': 'foreign'}):
        env = portal.environment(tmp_path, profile(), secret)
    assert env['SECRET_KEY'] == secret['SECRET_KEY']
    assert env['ACCOUNT_ENCRYPTION_KEY'] == secret['ACCOUNT_ENCRYPTION_KEY']
    assert env['GOOGLE_CLIENT_SECRET'] == ''
    assert not {'CLOUDFLARE_API_TOKEN', 'TOKEN_ENCRYPTION_KEY', 'TUNNEL_TOKEN'} & env.keys()
    assert 'pass%3A%40%2F%3F@' in env['DATABASE_URL']
    assert env['DEPLOYMENT_MODE'] == 'portal' and env['DEBUG'] == '0'


def test_unexpected_private_setting_is_rejected(tmp_path):
    (tmp_path / 'integrations.json').write_text(json.dumps({'DEBUG': '1'}))
    with pytest.raises(ValueError, match='integrations.json'):
        portal.load_settings(tmp_path, 'integrations.json', {'GOOGLE_CLIENT_ID'})
