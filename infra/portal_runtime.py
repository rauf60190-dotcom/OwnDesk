"""Central portal supervisor. Its private directory must never be shared with a tenant."""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import signal
import socket
import sys
import threading
from urllib.parse import quote

from native_runtime import ROOT, command, executable, launch, managed_database


def profile_for(data):
    value = json.loads((data / 'runtime-profile.json').read_text(encoding='utf-8'))
    if value.get('format') != 1 or value.get('public_hostname') != 'owndesk.work':
        raise ValueError('Invalid central portal profile')
    pg = value['postgres']
    ports = [value['api_port'], value['origin_port'], pg['port']]
    if len(set(ports)) != 3 or any(type(p) is not int or not 1024 <= p <= 65535 for p in ports):
        raise ValueError('Invalid or overlapping portal ports')
    for key in ('database', 'role', 'admin_role'):
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', pg[key]):
            raise ValueError('Invalid PostgreSQL identifier')
    if value.get('protocol', 'http2') not in {'http2', 'quic', 'auto'}:
        raise ValueError('Invalid tunnel protocol')
    if type(value.get('mail_enabled', False)) is not bool:
        raise ValueError('mail_enabled must be boolean')
    return value


def load_settings(data, filename, allowed):
    path = data / filename
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.keys() - allowed or any(not isinstance(v, str) for v in value.values()):
        raise ValueError('Invalid private settings: ' + filename)
    return value


def environment(data, profile, secret):
    pg = profile['postgres']
    required = ('SECRET_KEY', 'ACCOUNT_ENCRYPTION_KEY', 'POSTGRES_ADMIN_PASSWORD', 'POSTGRES_APP_PASSWORD')
    if any(not isinstance(secret.get(k), str) or not secret[k] for k in required):
        raise ValueError('Original portal secrets are required; keys will not be regenerated')
    # Credentials come only from protected portal files, never the launching user's environment.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('GOOGLE_', 'CLOUDFLARE_', 'EMAIL_', 'PG'))
           and k not in {'TUNNEL_TOKEN', 'TOKEN_ENCRYPTION_KEY', 'DATABASE_URL', 'ACCOUNT_ENCRYPTION_KEY'}}
    env.update(DJANGO_SETTINGS_MODULE='config.settings', PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
               PYTHONDONTWRITEBYTECODE='1', DEBUG='0', DEPLOYMENT_MODE='portal', OWNDESK_RUNTIME='distributed',
               PUBLIC_BASE_URL='https://owndesk.work', ALLOWED_HOSTS='owndesk.work',
               CSRF_TRUSTED_ORIGINS='https://owndesk.work', TRUST_LOOPBACK_CLIENT_IP='1',
               SECRET_KEY=secret['SECRET_KEY'], ACCOUNT_ENCRYPTION_KEY=secret['ACCOUNT_ENCRYPTION_KEY'],
               PUBLIC_REGISTRATION_ENABLED='0', LOCAL_EMAIL_PREVIEW='0', LOCAL_TELEGRAM_POLLING='0',
               PORTAL_RELEASE_DIR=str(data / 'releases'), EMAIL_HOST='', GOOGLE_CLIENT_ID='', GOOGLE_CLIENT_SECRET='',
               XDG_DATA_HOME=str(data / 'cache'), XDG_CONFIG_HOME=str(data / 'config'), PSYCOPG_IMPL='python')
    env['DATABASE_URL'] = (f"postgresql://{pg['role']}:{quote(secret['POSTGRES_APP_PASSWORD'], safe='')}"
                           f"@127.0.0.1:{pg['port']}/{pg['database']}")
    env['PATH'] = str((ROOT / pg['bin']).resolve()) + os.pathsep + env.get('PATH', '')
    env.update(load_settings(data, 'integrations.json', {
        'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'EMAIL_HOST', 'EMAIL_PORT', 'EMAIL_HOST_USER',
        'EMAIL_HOST_PASSWORD', 'EMAIL_USE_TLS', 'EMAIL_USE_SSL', 'DEFAULT_FROM_EMAIL', 'PUBLIC_REGISTRATION_ENABLED'}))
    env.update(load_settings(data, 'cloudflare.json', {
        'CLOUDFLARE_API_TOKEN', 'CLOUDFLARE_ACCOUNT_ID', 'CLOUDFLARE_ZONE_ID', 'INSTALLATIONS_DOMAIN'}))
    return env


def run(data):
    from local_snapshot import local_runtime_lock
    with local_runtime_lock(data / 'runtime-supervisor'):
        profile = profile_for(data)
        secret = json.loads((data / 'secrets.json').read_text(encoding='utf-8'))
        env = environment(data, profile, secret)
        caddy, cloudflared = executable(profile['caddy']), executable(profile['cloudflared'])
        for port in (profile['api_port'], profile['origin_port'], profile['postgres']['port']):
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', port))
        stop = threading.Event()
        stop_file = data / 'stop-request'
        stop_file.unlink(missing_ok=True)
        old_term = signal.signal(signal.SIGTERM, lambda *_: stop.set())
        try:
            with ExitStack() as stack:
                processes = [managed_database(stack, data, profile['postgres'], secret, env)]
                from native_restore import initial_restore
                initial_restore(data, profile, secret, env)
                manage = [sys.executable, ROOT / 'backend/manage.py']
                command([*manage, 'migrate', '--noinput'], env)
                command([*manage, 'check', '--deploy', '--fail-level', 'WARNING'], env)
                config = (ROOT / 'infra/Caddyfile.portal-native').read_text(encoding='utf-8')
                config = config.replace('__ORIGIN_PORT__', str(profile['origin_port']))
                config = config.replace('__API_PORT__', str(profile['api_port']))
                config = config.replace('__DIST__', (ROOT / 'frontend/dist').as_posix())
                proxy_file = data / 'Caddyfile'
                proxy_file.write_text(config, encoding='utf-8')
                processes.append(launch(stack, data, 'api', [sys.executable, '-m', 'daphne', '-b', '127.0.0.1',
                    '-p', str(profile['api_port']), 'config.asgi:application'], env))
                processes.append(launch(stack, data, 'caddy', [caddy, 'run', '--config', proxy_file,
                    '--adapter', 'caddyfile'], env))
                if profile.get('mail_enabled', False) and env.get('EMAIL_HOST') and env.get('DEFAULT_FROM_EMAIL'):
                    processes.append(launch(stack, data, 'mail', [*manage, 'shell', '-c',
                        'from threading import Event; from apps.accounts.tasks import run_local_mail; run_local_mail(Event())'], env))
                tunnel_started = False
                print('OwnDesk central portal started on loopback.', flush=True)
                while not stop.wait(1):
                    if stop_file.exists():
                        stop_file.unlink(missing_ok=True)
                        break
                    token_file = data / 'root-tunnel.token'
                    # Publication can be committed after local acceptance, without restarting the database.
                    if token_file.exists() and not tunnel_started:
                        token = token_file.read_text(encoding='utf-8').strip()
                        if len(token) < 32 or any(c.isspace() for c in token):
                            raise ValueError('Invalid root tunnel token file')
                        tunnel_env = {k: env[k] for k in ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP',
                            'XDG_CONFIG_HOME', 'XDG_DATA_HOME') if k in env}
                        tunnel_env['TUNNEL_TOKEN'] = token
                        processes.append(launch(stack, data, 'cloudflared', [cloudflared, 'tunnel', '--no-autoupdate',
                            '--protocol', profile.get('protocol', 'http2'), 'run'], tunnel_env))
                        tunnel_started = True
                    if any(p.poll() is not None for p in processes):
                        raise RuntimeError('A portal component stopped; see private logs')
        finally:
            signal.signal(signal.SIGTERM, old_term)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('run', 'stop'))
    parser.add_argument('--data', required=True)
    args = parser.parse_args()
    data = Path(args.data).resolve()
    if args.action == 'stop':
        (data / 'stop-request').touch()
    else:
        run(data)
