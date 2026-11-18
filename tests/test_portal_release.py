import hashlib
import json

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User


@pytest.mark.django_db
def test_portal_release_download_checks_session_mode_and_archive(tmp_path, settings):
    metadata_url = "/api/v1/portal/release"
    download_url = metadata_url + "/download"
    settings.PORTAL_RELEASE_DIR = str(tmp_path)
    settings.DEPLOYMENT_MODE = "portal"
    archive = tmp_path / "OwnDesk.zip"
    contents = b"synthetic release"
    archive.write_bytes(contents)
    metadata = {"version": "0.4.0", "sha256": hashlib.sha256(contents).hexdigest(), "size": len(contents)}
    (tmp_path / "release.json").write_text(json.dumps(metadata), encoding="utf-8")

    client = APIClient()
    for url in (metadata_url, download_url):
        assert client.get(url).status_code == 403
    client.force_login(User.objects.create_user(username="portal-release-test"))
    assert client.get(metadata_url).json() == metadata
    response = client.get(download_url)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    assert response["Content-Disposition"] == 'attachment; filename="OwnDesk.zip"'
    assert b"".join(response.streaming_content) == contents
    settings.DEPLOYMENT_MODE = "organization"
    assert client.get(metadata_url).status_code == 404
    assert client.get(download_url).status_code == 404
    settings.DEPLOYMENT_MODE = "portal"
    archive.write_bytes(b"x" * len(contents))
    assert client.get(metadata_url).status_code == 503
    assert client.get(download_url).status_code == 503
