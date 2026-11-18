"""Preserve notices from exact pinned vendor downloads, without executing their binaries."""
import argparse,hashlib,json,re
from pathlib import Path
from urllib.request import Request,urlopen
from zipfile import ZipFile

root=Path(__file__).resolve().parents[1]
UPSTREAM={
 'cloudflared':'https://raw.githubusercontent.com/cloudflare/cloudflared/2026.10.7/LICENSE',
 'winsw':'https://raw.githubusercontent.com/winsw/winsw/v2.12.0/LICENSE.txt'}

def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise SystemExit('Use a new output directory; existing notices are retained.')
    pins=json.loads((root/'infra/windows-dependencies.json').read_text())
    pins['python']=json.loads((root/'infra/windows-python.json').read_text())
    for name,item in pins.items():
        path=args.cache/item['filename']
        if not path.is_file() or path.stat().st_size!=item['size'] or sha(path)!=item['sha256']:
            raise SystemExit('Exact pinned vendor download is missing or differs: '+name)
    args.output.mkdir(parents=True)
    report={'status':'notices-collected-review-required','complete_native_sbom':False,'components':[]}
    for name,item in pins.items():
        component={'name':name,'version':item['version'],'download_url':item['url'],'download_sha256':item['sha256'],'pinned_download_verified':True,'notices':[]}
        target=args.output/name;target.mkdir()
        path=args.cache/item['filename']
        if path.suffix=='.zip':
            with ZipFile(path) as archive:
                selected=[entry for entry in archive.infolist() if not entry.is_dir() and re.search(r'(?:^|[._-])(licen[cs]es?|copying|copyright|notice)(?:[._-]|$)',Path(entry.filename).name,re.I)
                          and Path(entry.filename).suffix.lower() not in {'.exe','.dll','.pyd','.so','.py','.pyc','.js','.css','.map','.zip','.whl','.png','.jpg','.svg','.gif'}]
                if len(selected)>1000 or sum(entry.file_size for entry in selected)>32*1024*1024:raise ValueError('Unexpected notice volume')
                for entry in selected:
                    if entry.file_size>16*1024*1024:raise ValueError('Unexpected notice size')
                    content=archive.read(entry)
                    digest=hashlib.sha256(content).hexdigest()
                    filename=hashlib.sha256(entry.filename.encode()).hexdigest()[:12]+'-'+re.sub(r'[^a-zA-Z0-9._-]','_',Path(entry.filename).name)[-90:]
                    destination=target/filename;destination.write_bytes(content)
                    component['notices'].append({'source_in_archive':entry.filename,'path':destination.relative_to(args.output).as_posix(),'sha256':digest})
        else:
            url=UPSTREAM[name]
            if item['version'] not in url:raise ValueError('Upstream source version does not match pin')
            with urlopen(Request(url,headers={'User-Agent':'OwnDesk-license-review'}),timeout=30) as response:
                if response.geturl()!=url:raise ValueError('Unexpected upstream redirect')
                content=response.read(2*1024*1024+1)
            if len(content)>2*1024*1024 or not content.strip():raise ValueError('Invalid upstream notice')
            destination=target/'LICENSE.upstream.txt';destination.write_bytes(content)
            component['notices'].append({'upstream_url':url,'path':destination.relative_to(args.output).as_posix(),'sha256':hashlib.sha256(content).hexdigest()})
        if not component['notices']:raise ValueError('No upstream notice found for '+name)
        report['components'].append(component)
    (args.output/'inventory.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    lines=['# Native download notices — review draft','',
        'Collected from exact pinned vendor downloads and versioned upstream licenses. The original texts are preserved without changing their license.',
        'This is not a complete SBOM or proof of licensing compatibility for every embedded library.',
        '', '| Component | Version | Notice files | Pinned download SHA-256 |','|---|---|---:|---|']
    for component in report['components']:
        lines.append('| {name} | {version} | {count} | {download_sha256} |'.format(**component,count=len(component['notices'])))
    lines+=['','See inventory.json for each notice\'s exact source path/URL and SHA-256. PostgreSQL/EDB notices include bundled tools and libraries present in the vendor archive; their inclusion here does not assert every one is executed by OwnDesk.','',
        'Pending: source/provenance inventory for embedded native dependencies and distribution obligations. The license of OwnDesk-written code is recorded separately in the root LICENSE/LICENSING.md; upstream notices keep their own terms.']
    (args.output/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'components':len(report['components']),'notices':sum(len(item['notices']) for item in report['components']),'status':report['status']},indent=2))

if __name__=='__main__':main()
