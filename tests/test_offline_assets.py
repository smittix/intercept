"""Tests for the offline asset checker, notably its path containment."""


def _logged_in_get(client, path, **kwargs):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
    return client.get(path, **kwargs)


class TestCheckAssetContainment:
    """check_asset must not report on files outside static/vendor.

    The prefix test it used to rely on ("/static/vendor/") is not
    containment: "/static/vendor/../../../etc/passwd" satisfies it. The
    endpoint only returns a boolean, so this leaked file existence rather
    than contents, but it is still an oracle over the filesystem.
    """

    def test_legitimate_vendor_path_is_allowed(self, client):
        resp = _logged_in_get(
            client, "/offline/check-asset", query_string={"path": "/static/vendor/leaflet/leaflet.js"}
        )
        assert resp.status_code == 200
        assert resp.get_json()["exists"] is True

    def test_missing_vendor_file_reports_absent_not_error(self, client):
        resp = _logged_in_get(
            client, "/offline/check-asset", query_string={"path": "/static/vendor/nope/nothing.js"}
        )
        assert resp.status_code == 200
        assert resp.get_json()["exists"] is False

    def test_traversal_to_system_file_is_refused(self, client):
        resp = _logged_in_get(
            client,
            "/offline/check-asset",
            query_string={"path": "/static/vendor/../../../../../../etc/passwd"},
        )
        assert resp.status_code == 400

    def test_traversal_into_application_source_is_refused(self, client):
        resp = _logged_in_get(
            client, "/offline/check-asset", query_string={"path": "/static/vendor/../../app.py"}
        )
        assert resp.status_code == 400

    def test_traversal_to_sibling_static_dir_is_refused(self, client):
        resp = _logged_in_get(
            client, "/offline/check-asset", query_string={"path": "/static/vendor/../js/core/app.js"}
        )
        assert resp.status_code == 400

    def test_non_vendor_prefix_still_refused(self, client):
        resp = _logged_in_get(client, "/offline/check-asset", query_string={"path": "/etc/passwd"})
        assert resp.status_code == 400
