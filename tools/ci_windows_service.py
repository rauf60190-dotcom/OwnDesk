"""Real LocalService acceptance on a disposable GitHub Windows runner only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from uuid import uuid4
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True


def main():
    if (os.name != 'nt' or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_OS') != 'Windows'
            or os.environ.get('GITHUB_REPOSITORY') != 'rauf60190-dotcom/OwnDesk'):
        raise RuntimeError('This acceptance tool runs only in OwnDesk GitHub Windows CI')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    runner_temp = Path(os.environ['RUNNER_TEMP']).resolve()
    if not source.is_relative_to(runner_temp) or not output.is_relative_to(runner_temp) or output.exists():
        raise ValueError('Use fresh report and extracted source paths inside RUNNER_TEMP')
    metadata = json.loads(args.metadata.read_text(encoding='utf-8-sig'))
    if metadata['source_commit'] != os.environ['GITHUB_SHA']:
        raise ValueError('Payload commit does not match this run')
    sys.path.insert(0, str(source / 'infra'))
    import windows_service as service
    service.admin_required()
    identity = 'OwnDeskCI' + uuid4().hex
    app = Path(os.environ['ProgramFiles']) / 'OwnDeskAcceptance' / identity
    data = Path(os.environ['ProgramData']) / 'OwnDeskAcceptance' / identity
    host = app / 'OwnDeskService.exe'
    if app.exists() or data.exists():
        raise ValueError('Use fresh CI application and data directories')
    result = {'success': False, 'source_commit': metadata['source_commit'],
              'payload_sha256': metadata['sha256'], 'service': identity, 'stage': 'starting',
              'gui_tested': False, 'uac_tested': False, 'os_reboot_tested': False,
              'public_pairing_tested': False, 'public_release_approved': False}

    def checkpoint(stage, **values):
        result.update(stage=stage, **values)
        print('OwnDesk isolated acceptance: ' + stage, flush=True)

    def query():
        command = (f"Get-CimInstance Win32_Service -Filter \"Name='{identity}'\" | "
                   'Select-Object Name,StartName,StartMode,State,PathName | ConvertTo-Json -Compress')
        value = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
            capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        return json.loads(value.stdout) if value.stdout.strip() else None

    def owns(actual):
        return actual and actual['PathName'].strip().strip('"').casefold() == str(host).casefold()

    def ready(starts, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                status = json.loads((data / 'probe-status.json').read_text())
                if status['starts'] >= starts and status['state'] == 'ready' and time.time() - status['checked_at'] < timeout:
                    assert status['workspaces'] == 1
                    return status
            except (OSError, ValueError):
                pass
            time.sleep(1)
        raise RuntimeError('CI service readiness timed out')

    registration_attempted = False
    try:
        if query() is not None:
            raise RuntimeError('Refusing to replace an existing service')
        for port in (15586, 18000, 15173):
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', port))
        checkpoint('preparing-components')
        python, tools, _ = service.prepare_application(app, data, lambda _: None,
            cache=runner_temp / 'owndesk-vendor-cache', service_id=identity)
        verified = 0
        for part in ('backend', 'infra', 'frontend/dist'):
            for path in (source / part).rglob('*'):
                if path.is_file():
                    copied = app / path.relative_to(source)
                    if hashlib.sha256(copied.read_bytes()).digest() != hashlib.sha256(path.read_bytes()).digest():
                        raise RuntimeError('Installed source differs from the payload')
                    verified += 1
        result['installed_files_verified'] = verified
        import native_runtime as runtime
        organization = uuid4()
        runtime.save_private(data / 'portal-connection.json', {
            'id': str(organization), 'connector_key': 'synthetic-ci-never-used-for-a-portal-request'})
        runtime.save_private(data / 'runtime-profile.json', {'format': 1,
            'public_hostname': 'org-' + organization.hex + '.owndesk.work', 'api_port': 18000, 'protocol': 'http2',
            'caddy': str(tools['caddy']), 'postgres': {'bin': str(tools['postgres_bin']), 'port': 15586,
                'database': 'owndesk_ci', 'role': 'owndesk_ci_app', 'admin_role': 'owndesk_ci_dba'}})
        runtime.get_secrets(data, managed=True)
        shutil.copy2(source / 'tools/ci_service_probe.py', app / 'infra/ci_service_probe.py')
        shutil.copy2(tools['winsw'], host)
        xml = ET.fromstring(service.service_xml(app, data, python, service_id=identity,
            display_name='OwnDesk disposable CI acceptance'))
        xml.find('startarguments').text = subprocess.list2cmdline([str(app / 'infra/ci_service_probe.py'), '--data', str(data)])
        xml.find('stoparguments').text = subprocess.list2cmdline([str(app / 'infra/ci_service_probe.py'), '--data', str(data), '--stop'])
        (app / 'OwnDeskService.xml').write_text(ET.tostring(xml, encoding='unicode'), encoding='utf-8')
        checkpoint('registering-service')
        registration_attempted = True
        service.execute([host, 'install'], lambda _: None)
        actual = query()
        assert owns(actual) and actual['StartName'].lower() == r'nt authority\localservice'
        assert actual['StartMode'] == 'Auto'
        result.update(local_service_verified=True, automatic_start_verified=True)
        checkpoint('starting-application')
        service.execute([host, 'start'], lambda _: None)
        first = ready(1)
        checkpoint('checking-restart-and-persistence')
        service.execute([host, 'stopwait'], lambda _: None)
        service.execute([host, 'start'], lambda _: None)
        second = ready(first['starts'] + 1)
        result.update(first_start_verified=True, persistence_verified=True, graceful_stop_verified=True)
        checkpoint('checking-automatic-recovery')
        (data / 'crash-request').touch()
        third = ready(second['starts'] + 1)
        result.update(automatic_recovery_verified=True, starts=third['starts'])
        checkpoint('application-checks-passed', application_checks_passed=True)
    except Exception as error:
        checkpoint('failed', error_type=type(error).__name__)
        # Keep detailed diagnostics private to the disposable runner, not the artifact.
        import traceback
        result['failure_frames'] = [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                                    for frame in traceback.extract_tb(error.__traceback__)]
        if data.exists():
            (data / 'acceptance-error.log').write_text(traceback.format_exc(), encoding='utf-8')
    finally:
        if registration_attempted:
            try:
                actual = query()
                if actual is None:
                    result['temporary_service_removed'] = True
                elif not owns(actual):
                    raise RuntimeError('CI service identity changed; refusing to act')
                else:
                    if actual['State'] != 'Stopped':
                        service.execute([host, 'stopwait'], lambda _: None)
                    service.execute([host, 'uninstall'], lambda _: None)
                    result['temporary_service_removed'] = query() is None
            except Exception as error:
                result['cleanup_error_type'] = type(error).__name__
        result['success'] = bool(result.get('application_checks_passed') and result.get('temporary_service_removed'))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
