"""Unraid Community Applications template and first-run wizard."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent


class TestUnraidTemplate:
    def _root(self) -> ET.Element:
        path = ROOT / "unraid" / "intercept.xml"
        assert path.is_file()
        tree = ET.parse(path)
        return tree.getroot()

    def test_template_is_installable(self):
        root = self._root()
        assert root.tag == "Container"
        assert root.attrib.get("version") == "2"
        assert root.findtext("Name") == "INTERCEPT"
        assert root.findtext("Repository") == "ghcr.io/smittix/intercept:latest"
        assert root.findtext("Network") == "bridge"
        assert root.findtext("Privileged") == "true"
        assert "[PORT:5050]" in (root.findtext("WebUI") or "")
        assert "FUSE" in (root.findtext("Requires") or "")

    def test_template_maps_persistent_paths_and_usb(self):
        root = self._root()
        configs = {item.attrib.get("Target"): item for item in root.findall("Config")}
        assert configs["5050"].attrib.get("Type") == "Port"
        assert configs["/app/instance"].attrib.get("Default") == "/mnt/cache/appdata/intercept/instance"
        assert configs["/app/data"].attrib.get("Default") == "/mnt/user/appdata/intercept/data"
        assert configs["/config"].attrib.get("Default") == "/mnt/user/appdata/intercept/config"
        assert configs[""].attrib.get("Type") == "Device"
        assert configs[""].attrib.get("Default") == "/dev/bus/usb"
        assert (configs[""].text or "").strip() == "/dev/bus/usb"
        assert configs["INTERCEPT_ADMIN_PASSWORD"].attrib.get("Mask") == "true"
        assert configs["INTERCEPT_UNRAID"].attrib.get("Default") == "true"
        assert configs["INTERCEPT_ENV_FILE"].attrib.get("Default") == "/config/.env"
        assert configs["INTERCEPT_INSTANCE_DIR"].attrib.get("Default") == "/app/instance"


class TestUnraidHelpers:
    def test_env_flags_detect_unraid_and_docker(self, tmp_path, monkeypatch):
        from utils.unraid import running_in_docker, running_on_unraid

        monkeypatch.delenv("INTERCEPT_UNRAID", raising=False)
        monkeypatch.delenv("INTERCEPT_DOCKER", raising=False)
        with patch("utils.unraid._UNRAID_MARKERS", (tmp_path / "missing",)):
            assert running_on_unraid() is False
        monkeypatch.setenv("INTERCEPT_UNRAID", "true")
        assert running_on_unraid() is True
        monkeypatch.setenv("INTERCEPT_DOCKER", "1")
        assert running_in_docker() is True

    def test_fuse_paths_use_delete_journal(self, tmp_path, monkeypatch):
        from utils.unraid import recommended_sqlite_journal_mode

        db_path = tmp_path / "intercept.db"
        db_path.write_text("", encoding="utf-8")
        monkeypatch.delenv("INTERCEPT_SQLITE_JOURNAL", raising=False)
        with patch("utils.unraid.filesystem_type", return_value="fuse.shfs"):
            assert recommended_sqlite_journal_mode(db_path) == "DELETE"
        with patch("utils.unraid.filesystem_type", return_value="ext4"):
            assert recommended_sqlite_journal_mode(db_path) == "WAL"
        monkeypatch.setenv("INTERCEPT_SQLITE_JOURNAL", "DELETE")
        with patch("utils.unraid.filesystem_type", return_value="ext4"):
            assert recommended_sqlite_journal_mode(db_path) == "DELETE"

    def test_filesystem_type_picks_longest_mount(self, tmp_path, monkeypatch):
        from utils import unraid as unraid_mod

        mount_file = tmp_path / "mounts"
        target = tmp_path / "appdata" / "instance"
        target.mkdir(parents=True)
        mount_file.write_text(
            "rootfs / rootfs rw 0 0\n"
            f"/dev/md1 {tmp_path / 'appdata'} fuse.shfs rw 0 0\n",
            encoding="utf-8",
        )
        real_path = Path

        def path_factory(value=".", *args, **kwargs):
            if str(value) == "/proc/mounts":
                return mount_file
            return real_path(value, *args, **kwargs)

        monkeypatch.setattr(unraid_mod, "Path", path_factory)
        assert unraid_mod.filesystem_type(target / "intercept.db") == "fuse.shfs"

    def test_instance_dir_env_override(self, tmp_path, monkeypatch):
        from utils import database as db

        monkeypatch.setenv("INTERCEPT_INSTANCE_DIR", str(tmp_path))
        assert db.get_instance_dir() == tmp_path
        assert db.get_db_path() == tmp_path / "intercept.db"


class TestSetupWizardRoutes:
    def test_status_and_complete(self, client, monkeypatch):
        monkeypatch.setenv("INTERCEPT_UNRAID", "true")
        monkeypatch.setenv("INTERCEPT_DOCKER", "true")
        with patch("routes.setup.usb_passthrough_present", return_value=True), patch(
            "routes.setup.get_setting", return_value=False
        ), patch("routes.setup.set_setting") as mock_set:
            status = client.get("/setup/status")
            assert status.status_code == 200
            payload = status.get_json()
            assert payload["status"] == "success"
            assert payload["platform"]["unraid"] is True
            assert payload["platform"]["docker"] is True
            assert payload["platform"]["usb_passthrough"] is True
            assert payload["complete"] is False
            assert "journal_mode" in payload["storage"]

            done = client.post("/setup/complete")
            assert done.status_code == 200
            assert done.get_json()["complete"] is True
            mock_set.assert_called()


class TestPackagingDocs:
    def test_compose_and_image_persist_instance(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        start = (ROOT / "start.sh").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "./instance:/app/instance" in compose
        assert "INTERCEPT_INSTANCE_DIR=/app/instance" in compose
        assert "INTERCEPT_INSTANCE_DIR=/app/instance" in dockerfile
        assert "INTERCEPT_ENV_FILE" in start
        assert "unraid/intercept.xml" in readme
        assert "/setup/status" in readme
        wizard = (ROOT / "static" / "js" / "core" / "first-run-setup.js").read_text(encoding="utf-8")
        assert "/setup/status" in wizard
        assert "/setup/complete" in wizard
        assert "Unraid / USB SDR" in wizard
