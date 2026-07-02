from fastapi.testclient import TestClient

from vinted_monitor.api.main import app


def test_action_requests_are_disabled_by_default() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/actions",
        json={"item_id": 1, "action_type": "purchase", "payload": {}},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Action requests are disabled"
