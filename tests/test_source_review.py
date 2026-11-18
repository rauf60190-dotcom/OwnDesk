import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('source_review',Path(__file__).resolve().parents[1]/'tools/prepare_source_review.py')
review=importlib.util.module_from_spec(spec);spec.loader.exec_module(review)

def test_runtime_and_secret_files_never_enter_export(tmp_path):
    source=tmp_path/'source';source.mkdir()
    files={'backend/app.py':'print("source")','LICENSE':'MIT License','.env.example':'SECRET_KEY=\n',
        '.env':'secret','.env.production':'secret','backend/dev.sqlite3':'database',
        '.runtime/secrets.json':'private','node_modules/package/index.js':'dependency',
        'config/secrets.json':'private','runtime-profile.json':'private',
        'frontend/dist/index.html':'build','logs/setup.log.1':'private',
        'third_party/licenses/LICENSE.txt':'public upstream notice'}
    for name,content in files.items():
        path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
    result=review.export(source,tmp_path/'review')
    assert set(result['files'])=={'backend/app.py','LICENSE','.env.example','third_party/licenses/LICENSE.txt'}
    assert not result['high_confidence_findings'] and not result['published']

def test_token_in_source_is_reported_without_disclosing_its_value(tmp_path):
    source=tmp_path/'source';source.mkdir()
    token='ghp_'+'A'*36
    (source/'app.py').write_text('api_key="'+token+'"')
    result=review.export(source,tmp_path/'review')
    assert result['high_confidence_findings']==[{'path':'app.py','line':1,'kind':'github-token'}]
    assert token not in str(result)

def test_unreviewed_media_is_excluded_and_fresh_destination_required(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'private-photo.png').write_bytes(b'not a real image')
    output=tmp_path/'review'
    assert not review.export(source,output)['files']
    with pytest.raises(ValueError):review.export(source,output)
    with pytest.raises(ValueError):review.export(source,source/'nested')
