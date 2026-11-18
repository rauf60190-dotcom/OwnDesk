"""Create a local source-review export. Does not publish, choose a license, or copy runtime data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

ROOT=Path(__file__).resolve().parents[1]
EXCLUDED_DIRS={'.git','.runtime','node_modules','__pycache__','.pytest_cache','.venv','venv','runtime','dist',
    'staticfiles','media','private-attachments','backups','releases','test-results','playwright-report','.ruff_cache','.idea','.vscode'}
PRIVATE_NAMES={'secrets.json','runtime-profile.json','integrations.json','cloudflare.json','mail-settings.json',
    'root-tunnel.token','installation-code.txt'}
PRIVATE_SUFFIXES={'.sqlite3','.sqlite','.db','.dump','.bak','.log','.pyc','.pyo','.pfx','.p12','.pem','.key',
    '.exe','.dll','.pyd','.zip','.7z','.tar','.gz','.pid','.tsbuildinfo'}
IMAGE_SUFFIXES={'.png','.jpg','.jpeg','.webp','.gif','.svg','.ico'}
PATTERNS={
    'private-key':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----'),
    'github-token':re.compile(r'(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{40,})'),
    'google-oauth-secret':re.compile(r'GOCSPX-[A-Za-z0-9_-]{20,}'),
    'resend-key':re.compile(r'(?<![A-Za-z0-9_])re_[A-Za-z0-9]{24,}'),
    'telegram-bot-token':re.compile(r'(?<![A-Za-z0-9])\d{8,12}:[A-Za-z0-9_-]{32,}'),
    'aws-access-key':re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'static-bearer-jwt':re.compile(r'eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}'),
}

def excluded(relative):
    if any(part.lower() in EXCLUDED_DIRS for part in relative.parts):return True
    name=relative.name.lower()
    return (name in PRIVATE_NAMES or name.startswith('portal-connection.json') or
        (name.startswith('.env') and name!='.env.example') or relative.suffix.lower() in PRIVATE_SUFFIXES or
        '.sqlite3-' in name or '.db-' in name or '.log.' in name or name.endswith('.worker.lock'))

def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def scan_text(path,relative):
    if path.suffix.lower() in IMAGE_SUFFIXES:return []
    text=path.read_bytes().decode('utf-8',errors='replace')
    result=[]
    for kind,pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            result.append({'path':str(relative).replace('\\','/'),'line':text.count('\n',0,match.start())+1,'kind':kind})
    return result

def export(source,output):
    if source.is_symlink() or source.is_junction():raise ValueError('Source root is a link')
    source=source.resolve();output=output.absolute()
    if output.exists() or output.resolve().is_relative_to(source):raise ValueError('Use a fresh destination outside the source tree')
    for parent in output.parents:
        if parent.is_symlink() or parent.is_junction():raise ValueError('Destination must not traverse links')
    policy=source/'tools/source-assets-reviewed.json'
    assets=json.loads(policy.read_text(encoding='utf-8')).get('files',{}) if policy.is_file() else {}
    selected=[];skipped=[];findings=[];total=0
    for directory,folders,files in os.walk(source,followlinks=False):
        base=Path(directory)
        for folder in list(folders):
            target=base/folder;relative=target.relative_to(source)
            if excluded(relative) or target.is_symlink() or target.is_junction():
                folders.remove(folder);skipped.append({'path':relative.as_posix(),'kind':'excluded-directory'})
        for name in sorted(files):
            path=base/name;relative=path.relative_to(source)
            if excluded(relative) or path.is_symlink() or path.is_junction() or path.stat().st_nlink!=1:
                skipped.append({'path':relative.as_posix(),'kind':'excluded-file'});continue
            if path.suffix.lower() in IMAGE_SUFFIXES and assets.get(relative.as_posix(),{}).get('sha256')!=digest(path):
                skipped.append({'path':relative.as_posix(),'kind':'unreviewed-media'});continue
            size=path.stat().st_size
            if size>32*1024*1024:raise ValueError('Unexpected source file size: '+relative.as_posix())
            total+=size
            if len(selected)>=5000 or total>128*1024*1024:raise ValueError('Source export exceeds review limits')
            selected.append((path,relative,digest(path)))
            findings.extend(scan_text(path,relative))
    output.mkdir(parents=True)
    copied={}
    for path,relative,sha in selected:
        target=output/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
        if digest(target)!=sha:raise RuntimeError('File changed during export: '+relative.as_posix())
        copied[relative.as_posix()]={'size':target.stat().st_size,'sha256':sha}
    return {'status':'local-review-only','published':False,'network_operations':0,'files':copied,'bytes':total,
        'excluded':skipped,'high_confidence_findings':findings,'reviewed_media_count':sum(Path(name).suffix.lower() in IMAGE_SUFFIXES for name in copied),
        'project_license_present':(output/'LICENSE').is_file(),'public_release_approved':False}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if args.report.exists():raise SystemExit('Review report already exists')
    report=export(args.source,args.output)
    args.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'status':report['status'],'files':len(report['files']),'bytes':report['bytes'],
        'findings':len(report['high_confidence_findings']),'excluded_entries':len(report['excluded']),
        'project_license_present':report['project_license_present'],'published':False},indent=2))
    raise SystemExit(2 if report['high_confidence_findings'] else 0)

if __name__=='__main__':main()
