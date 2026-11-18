"""Check tracked source files and retained license notices; never print matched secrets."""
import hashlib
import json
from pathlib import Path
import subprocess

from prepare_source_review import ROOT, excluded, scan_text


def main():
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    findings = []
    for name in filter(None, names):
        relative = Path(name)
        path = ROOT / relative
        if excluded(relative) or path.is_symlink():
            findings.append({'path': name, 'kind': 'excluded-file'})
        else:
            findings.extend(scan_text(path, relative))
    checked = 0
    for metadata in ['third_party/inventory.json', 'third_party/native/inventory.json']:
        inventory = json.loads((ROOT / metadata).read_text(encoding='utf-8-sig'))
        if 'packages' in inventory:
            notices = [('third_party/' + item['path'], item['sha256'])
                       for package in inventory['packages'] for item in package['license_files']]
        else:
            notices = [('third_party/native/' + item['path'], item['sha256'])
                       for component in inventory['components'] for item in component['notices']]
        for name, expected in notices:
            path = ROOT / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                findings.append({'path': name, 'kind': 'notice-integrity'})
            checked += 1
    print(json.dumps({'findings': findings, 'notices_checked': checked,
                     'scope': 'Selected secret patterns and notice integrity; not a security audit'}, indent=2))
    return bool(findings)


if __name__ == '__main__':
    raise SystemExit(main())
