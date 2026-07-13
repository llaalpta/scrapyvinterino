from api_client import authenticated_test_client


def test_action_requests_are_disabled_by_default() -> None:
    client = authenticated_test_client()
    response = client.post(
        "/api/actions",
        json={"item_id": 1, "action_type": "purchase", "payload": {}},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Action requests are disabled"
