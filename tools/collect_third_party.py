"""Collect installed license texts for local review; never chooses the project's license."""
import argparse
from collections import Counter
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import shutil

root=Path(__file__).resolve().parents[1]

def slug(value):
    return re.sub(r'[^a-zA-Z0-9._-]', '_', value)

def legal_file(path):
    return bool(re.match(r'^(LICENSE|LICENCE|COPYING|NOTICE|AUTHORS)(?:$|[._-])',path.name,re.I))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    output=args.output.resolve()
    if output.exists():raise SystemExit('Use a fresh review directory; never overwrite accepted notices.')
    records=[]
    for requirement in (root/'backend/requirements.txt').read_text().splitlines():
        if not requirement.strip() or '==' not in requirement:continue
        name,pin=requirement.split('==',1)
        package=metadata.distribution(name)
        if package.version!=pin:raise SystemExit('Installed Python version differs from pin: '+name)
        files=[]
        for relative in package.files or []:
            relative=Path(str(relative))
            if not any(part.endswith('.dist-info') for part in relative.parts):continue
            if legal_file(relative) or 'licenses' in relative.parts:
                source=Path(package.locate_file(relative))
                if source.is_file():files.append((source,Path('python')/slug(name+'-'+pin)/relative.name))
        expression=package.metadata.get('License-Expression') or next(iter(package.metadata.get('License','').splitlines()),'')[:160]
        classifiers=[value.rsplit(' :: ',1)[-1] for value in package.metadata.get_all('Classifier',[]) if value.startswith('License ::')]
        records.append({'ecosystem':'python','name':name,'version':pin,'license_metadata':expression or '; '.join(classifiers) or 'UNKNOWN','sources':files})
    seen=set()
    for path in (root/'frontend/node_modules/.pnpm').rglob('package.json'):
        if path.parent.parent.name!='node_modules' and path.parent.parent.parent.name!='node_modules':continue
        data=json.loads(path.read_text(encoding='utf-8'))
        key=(data.get('name'),data.get('version'))
        if not all(key) or key in seen:continue
        seen.add(key)
        license=data.get('license',data.get('licenses','UNKNOWN'))
        if isinstance(license,dict):license=license.get('type','UNKNOWN')
        if not isinstance(license,str):license=json.dumps(license)
        files=[]
        for source in path.parent.iterdir():
            if source.is_file() and legal_file(source):files.append((source,Path('npm')/slug('-'.join(key))/source.name))
            elif source.is_dir() and not source.is_symlink() and source.name.lower() in {'license','licenses'}:
                files.extend((file,Path('npm')/slug('-'.join(key))/file.relative_to(path.parent)) for file in source.rglob('*') if file.is_file() and not file.is_symlink())
        records.append({'ecosystem':'npm','name':key[0],'version':key[1],'license_metadata':license,'sources':files})
    output.mkdir(parents=True)
    for record in records:
        record['license_files']=[]
        for source,relative in record.pop('sources'):
            destination=output/'licenses'/relative
            destination.parent.mkdir(parents=True,exist_ok=True)
            if destination.exists() and destination.read_bytes()!=source.read_bytes():
                destination=destination.with_name(hashlib.sha256(source.read_bytes()).hexdigest()[:12]+'-'+destination.name)
            shutil.copyfile(source,destination)
            record['license_files'].append({'path':destination.relative_to(output).as_posix(),'sha256':hashlib.sha256(destination.read_bytes()).hexdigest()})
    records.sort(key=lambda value:(value['ecosystem'],value['name'].casefold(),value['version']))
    report={'status':'review-required','scope':'Installed Python pins and all installed frontend metadata (including dev tools). Not a complete native-binary or compatibility audit.',
            'package_counts':dict(Counter(item['ecosystem'] for item in records)),
            'missing_license_texts':[item['ecosystem']+':'+item['name'] for item in records if not item['license_files']],
            'project_license_selected':(root/'LICENSE').is_file(),'native_binaries_review_complete':False,'packages':records}
    (output/'inventory.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    lines=['# Third-party notices — review draft','',
        'This directory preserves installed upstream license notices. It does not relicense dependencies, assign an OwnDesk license, or certify SignPath eligibility.',
        '', 'Python package versions match backend/requirements.txt. Frontend entries include build/test tools; metadata alone does not establish which files ship in browser bundles.',
        '', 'Pending: native binaries and their embedded libraries, assets, missing license texts, LGPL/MPL obligations and project license selection.',
        '', '| Ecosystem | Package | Version | Upstream license metadata | License texts |','|---|---|---|---|---|']
    for item in records:
        links=', '.join('[%s](%s)'%(Path(file['path']).name,file['path']) for file in item['license_files']) or '**MISSING: REVIEW**'
        lines.append('| {ecosystem} | {name} | {version} | {license} | {links} |'.format(**item,license=item['license_metadata'].replace('|','\\|'),links=links))
    (output/'THIRD-PARTY-NOTICES.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({key:value for key,value in report.items() if key!='packages'},indent=2))

if __name__=='__main__':main()
