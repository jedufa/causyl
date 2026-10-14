from pathlib import Path

from infra.webui.server_http_core import _admin_run_snapshot_script


def test_admin_archive_runner_prefers_tools_bin_archive(tmp_path: Path) -> None:
    repo_root = tmp_path
    archives_dir = repo_root / "archives"
    archives_dir.mkdir()
    expected_zip = archives_dir / "archive-test.zip"
    expected_zip.write_bytes(b"zip")

    bin_dir = repo_root / "tools" / "bin"
    bin_dir.mkdir(parents=True)
    archive_script = bin_dir / "archive"
    archive_script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo SNAPSHOT_ZIP_PATH=archives/archive-test.zip\n",
        encoding="utf-8",
    )

    zip_path, stdout, stderr, rc = _admin_run_snapshot_script(repo_root, debug=False)

    assert rc == 0
    assert stderr == ""
    assert zip_path == expected_zip.resolve()
    assert "SNAPSHOT_ZIP_PATH=archives/archive-test.zip" in stdout
