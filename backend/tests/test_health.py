from starlette.testclient import TestClient

from app.main import create_app


def test_health_ok():
    client = TestClient(create_app())
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
