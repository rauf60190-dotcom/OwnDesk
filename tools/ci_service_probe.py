"""Synthetic service process for the disposable Windows CI runner; no portal or tunnel."""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen

import native_runtime as runtime

parser = argparse.ArgumentParser()
parser.add_argument('--data', type=Path, required=True)
parser.add_argument('--stop', action='store_true')
args = parser.parse_args()
data = args.data
if args.stop:
    (data / 'probe-stop').touch()
    raise SystemExit(0)
(data / 'probe-stop').unlink(missing_ok=True)
profile = runtime.profile_for(data)
env = runtime.environment(data, profile, runtime.get_secrets(data, managed=True))
os.environ.update(env)
import django
django.setup()

with ExitStack() as stack:
    pg = runtime.managed_database(stack, data, profile['postgres'], runtime.get_secrets(data, managed=True), env)
    manage = [sys.executable, runtime.ROOT / 'backend/manage.py']
    runtime.command([*manage, 'migrate', '--noinput'], env)
    runtime.command([*manage, 'check', '--deploy', '--fail-level', 'WARNING'], env)
    runtime.command([*manage, 'prepare_installation', '--output', data / 'installation-code.txt'], env)
    from apps.workspaces.models import Workspace
    workspace, _ = Workspace.objects.get_or_create(name='Isolated CI organization — synthetic data')
    from apps.teams.models import Team
    from apps.conversations.models import Conversation, Message
    from apps.tickets.models import Ticket
    from apps.attachments.models import Attachment
    team, _ = Team.objects.get_or_create(workspace=workspace, name='Synthetic CI support')
    conversation, _ = Conversation.objects.get_or_create(workspace=workspace, title='Synthetic backup conversation')
    ticket, _ = Ticket.objects.get_or_create(workspace=workspace, number=1, defaults={
        'team': team, 'conversation': conversation, 'subject': 'Synthetic backup ticket',
        'description': 'Snapshot before the later live update'})
    message, _ = Message.objects.get_or_create(workspace=workspace, conversation=conversation,
        ticket=ticket, text='Synthetic CI message; no customer or integration', direction='inbound')
    for name, content in [('note.txt', b'OwnDesk synthetic backup note\n'),
                          ('attachment.bin', bytes(range(256)) * 64)]:
        key = 'ci-fixtures/' + name
        path = data / 'private-attachments' / key
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            assert path.read_bytes() == content
        else:
            with path.open('xb') as stream: stream.write(content)
        Attachment.objects.get_or_create(workspace=workspace, object_key=key, defaults={
            'message': message, 'filename': name, 'size': len(content),
            'mime_type': 'application/octet-stream', 'state': 'ready'})
    api = runtime.launch(stack, data, 'api', [*manage, 'run_native', '--port', '18000'], env)
    config = (runtime.ROOT / 'infra/Caddyfile.connector').read_text()
    config = config.replace('{\n', '{\n    admin off\n    persist_config off\n', 1)
    config = config.replace(':5173', ':15173').replace('127.0.0.1:8000', '127.0.0.1:18000')
    (data / 'probe-Caddyfile').write_text(config)
    proxy = runtime.launch(stack, data, 'caddy', [profile['caddy'], 'run', '--config', data / 'probe-Caddyfile', '--adapter', 'caddyfile'], env)
    deadline = time.monotonic() + 90
    while True:
        if api.poll() is not None or pg.poll() is not None or proxy.poll() is not None:
            raise RuntimeError('CI application child stopped before readiness')
        try:
            request = Request('http://127.0.0.1:15173/api/v1/health/live', headers={
                'Host': profile['public_hostname'], 'X-Forwarded-Proto': 'https'})
            with urlopen(request, timeout=2) as response:
                if response.status == 200:
                    break
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError('CI application readiness timed out')
        time.sleep(.5)
    with urlopen(Request('http://127.0.0.1:15173/', headers={'Host': profile['public_hostname']}), timeout=3) as response:
        assert response.status == 200 and b'<html' in response.read(4096)
    status = data / 'probe-status.json'
    starts = json.loads(status.read_text()).get('starts', 0) if status.exists() else 0
    runtime.save_private(status, {'state': 'ready', 'starts': starts + 1,
        'workspaces': Workspace.objects.count(), 'checked_at': time.time()})
    while not (data / 'probe-stop').exists():
        if (data / 'crash-request').exists():
            (data / 'crash-request').unlink()
            raise RuntimeError('Intentional CI failure to verify automatic service recovery')
        if any(child.poll() is not None for child in (api, pg, proxy)):
            raise RuntimeError('CI application child exited')
        time.sleep(.5)
