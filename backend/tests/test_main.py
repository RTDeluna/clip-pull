import main as main_module
from fastapi.testclient import TestClient


def test_health_returns_ok():
    client = TestClient(main_module.app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cors_headers_present_for_cross_origin_request():
    client = TestClient(main_module.app)
    response = client.get("/health", headers={"Origin": "http://example.com"})
    assert response.headers.get("access-control-allow-origin") == "*"
