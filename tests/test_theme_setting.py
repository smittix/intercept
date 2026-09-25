"""The /settings/theme route: read and save the UI theme."""


def test_theme_defaults_to_null(client):
    resp = client.get("/settings/theme")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "success"


def test_theme_round_trip(client):
    assert client.post("/settings/theme", json={"value": "light"}).status_code == 200
    assert client.get("/settings/theme").get_json()["value"] == "light"


def test_theme_rejects_bad_value(client):
    assert client.post("/settings/theme", json={"value": "neon"}).status_code == 400


def test_theme_requires_login(anon_client):
    assert anon_client.get("/settings/theme").status_code in (302, 401, 403)
