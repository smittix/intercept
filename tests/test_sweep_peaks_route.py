"""GET /subghz/sweep/peaks: the strongest reading kept for each frequency in the last sweep."""
from unittest.mock import MagicMock, patch


class TestSweepPeaksRoute:
    def test_returns_the_kept_peaks_as_a_sorted_list(self, client):
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        with patch("routes.subghz.get_subghz_manager") as mock_get:
            mgr = MagicMock()
            mgr.sweep_peaks.return_value = {433.92: -40.0, 315.0: -55.5}
            mock_get.return_value = mgr
            resp = client.get("/subghz/sweep/peaks")
        assert resp.status_code == 200
        body = resp.get_json()
        # strongest first, so a reader that sees only the top of the list still sees the best signal
        assert body["peaks"] == [{"freq": 433.92, "power": -40.0}, {"freq": 315.0, "power": -55.5}]
        assert body["count"] == 2

    def test_empty_when_no_sweep_has_run(self, client):
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        with patch("routes.subghz.get_subghz_manager") as mock_get:
            mgr = MagicMock()
            mgr.sweep_peaks.return_value = {}
            mock_get.return_value = mgr
            resp = client.get("/subghz/sweep/peaks")
        assert resp.get_json() == {"peaks": [], "count": 0}
