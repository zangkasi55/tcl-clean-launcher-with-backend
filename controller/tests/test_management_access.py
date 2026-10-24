import pytest
from fastapi.testclient import TestClient

from tvcontrol.api import create_app


@pytest.mark.parametrize(("address", "expected"), [
    ("192.168.1.20", 200), ("192.168.1.21", 403),
])
def test_optional_parent_device_allowlist_preserves_passwordless_viewing(
        tmp_path, device, monkeypatch, address, expected):
    monkeypatch.setenv("TVCONTROL_MANAGEMENT_NETWORKS", "192.168.1.20/32")
    app = create_app(tmp_path, device, require_login=False, secure_cookie=False, run_monitor=False)
    with TestClient(app, client=(address, 12345)) as client:
        session = client.get("/api/session")
        assert session.status_code == 200
        assert session.json()["login_required"] is False
        client.headers["X-CSRF-Token"] = session.json()["csrf_token"]
        assert client.get("/api/status").status_code == 200
        response = client.put("/api/rule", json={"minutes": 45},
                              headers={"X-Forwarded-For": "192.168.1.20"})
        assert response.status_code == expected


def test_invalid_management_networks_fail_explicitly(tmp_path, device, monkeypatch):
    monkeypatch.setenv("TVCONTROL_MANAGEMENT_NETWORKS", "not-a-network")
    with pytest.raises(ValueError):
        create_app(tmp_path, device, run_monitor=False)
