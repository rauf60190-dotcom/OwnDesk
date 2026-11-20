"""Online backup and quarantined restore acceptance; disposable OwnDesk Windows CI only."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import traceback
from urllib.request import Request, urlopen

sys.dont_write_bytecode = True


def main():
    if (os.name != 'nt' or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('GITHUB_REPOSITORY') != 'rauf60190-dotcom/OwnDesk'):
        raise RuntimeError('This backup acceptance tool runs only in OwnDesk GitHub Windows CI')
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('app', 'data', 'backup', 'cache', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    app, data, backup, cache, output = (getattr(args, name).resolve()
        for name in ('app', 'data', 'backup', 'cache', 'output'))
    runner_temp = Path(os.environ['RUNNER_TEMP']).resolve()
    if (not data.is_relative_to(Path(os.environ['ProgramData']) / 'OwnDeskAcceptance')
            or not app.is_relative_to(Path(os.environ['ProgramFiles']) / 'OwnDeskAcceptance')
            or backup.parent != data.parent or backup == data or not backup.name.endswith('Backup')
            or not output.is_relative_to(runner_temp) or output.exists() or not cache.is_relative_to(runner_temp)):
        raise ValueError('Use the scoped CI installation and a fresh report')
    sys.path.insert(0, str(app / 'infra'))
    import native_backup
    import windows_recovery
    import windows_service
    import native_runtime
    windows_service.admin_required()
    result = {'success': False, 'source_commit': os.environ['GITHUB_SHA'], 'stage': 'backup',
              'restored_app_started': False, 'live_database_replaced': False}
    try:
        manifest = native_backup.backup(data, backup, (app / 'VERSION').read_text().strip())
        assert manifest['ready_attachments'] == 2
        assert manifest['counts']['tickets_ticket'] == 1 and manifest['counts']['conversations_message'] == 1
        profile = json.loads((data / 'runtime-profile.json').read_text())
        secret = native_runtime.get_secrets(data, managed=True)
        import psycopg
        pg = profile['postgres']
        def connection():
            return psycopg.connect(host='127.0.0.1', port=pg['port'], dbname=pg['database'], user=pg['role'],
                password=secret['POSTGRES_APP_PASSWORD'], connect_timeout=5)
        later = 'Synthetic live update after the backup snapshot'
        with connection() as database:
            assert database.execute('UPDATE tickets_ticket SET description=%s WHERE number=1', (later,)).rowcount == 1
        result.update(stage='quarantined-restore', backup_tables=len(manifest['counts']),
                      backup_attachments=manifest['ready_attachments'], online_backup_verified=True)
        # Reuse the verified download cache, while keeping the normal preparation/hash checks.
        original_prepare = windows_service.prepare_application
        def cached_prepare(*values, **options):
            options.setdefault('cache', cache)
            return original_prepare(*values, **options)
        windows_service.prepare_application = cached_prepare
        selected_hash = native_backup.fingerprint(backup / 'manifest.json')['sha256']
        recovery = windows_recovery.recover(backup, selected_hash)
        assert recovery['status'] == 'verified' and recovery['quarantine'] is True
        assert recovery['keys_preserved'] and recovery['original_config_protected']
        assert recovery['attachments'] == 2 and recovery['tables'] == len(manifest['counts'])
        assert recovery['temporary_service_removed'] and recovery['database_stopped']
        assert recovery['external_services_started'] is False
        assert re.fullmatch(r'[a-f0-9]{32}', recovery['recovery'])
        missing = subprocess.run(['sc.exe', 'query', 'OwnDeskRecovery' + recovery['recovery']],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        assert missing.returncode == 1060, 'Recovery service registration still exists or could not be checked'
        with connection() as database:
            assert database.execute('SELECT description FROM tickets_ticket WHERE number=1').fetchone()[0] == later
            assert database.execute('SELECT count(*) FROM workspaces_workspace').fetchone()[0] == 1
        with urlopen(Request('http://127.0.0.1:15173/api/v1/health/live', headers={
                'Host': profile['public_hostname'], 'X-Forwarded-Proto': 'https'}), timeout=5) as response:
            assert response.status == 200
        assert native_backup.verify(backup)['files'] == manifest['files']
        result.update(success=True, stage='verified', restored_tables=recovery['tables'],
            restored_attachments=recovery['attachments'], keys_preserved=True, original_config_protected=True,
            recovery_service_removed=True, recovery_database_stopped=True,
            post_snapshot_live_update_preserved=True, live_api_still_available=True,
            external_services_started=False)
    except Exception as error:
        result.update(error_type=type(error).__name__, failure_frames=[
            {'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
            for frame in traceback.extract_tb(error.__traceback__)])
        (backup / 'ci-error.log').write_text(traceback.format_exc(), encoding='utf-8')
    finally:
        output.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
