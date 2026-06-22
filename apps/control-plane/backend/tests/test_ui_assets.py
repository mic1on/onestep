from collections.abc import Generator
from contextlib import contextmanager

from onestep_control_plane_api.core.settings import settings


@contextmanager
def override_ui_settings(tmp_path) -> Generator[None, None, None]:
    original_dist_dir = settings.ui_dist_dir
    original_api_base_url = settings.ui_api_base_url
    dist_dir = tmp_path / "frontend-dist"
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text(
        '<!doctype html><html><body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    (assets_dir / "app.js").write_text("console.log('ok');", encoding="utf-8")
    settings.ui_dist_dir = str(dist_dir)
    settings.ui_api_base_url = "/"
    try:
        yield
    finally:
        settings.ui_dist_dir = original_dist_dir
        settings.ui_api_base_url = original_api_base_url


def test_app_config_js_uses_runtime_api_base_url(client, tmp_path) -> None:
    with override_ui_settings(tmp_path):
        settings.ui_api_base_url = "/plane/"

        response = client.get("/app-config.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert 'apiBaseUrl": "/plane/"' in response.text


def test_serves_vite_asset_from_configured_dist_dir(client, tmp_path) -> None:
    with override_ui_settings(tmp_path):
        response = client.get("/assets/app.js")

    assert response.status_code == 200
    assert "console.log('ok')" in response.text


def test_spa_fallback_serves_index_for_console_routes(client, tmp_path) -> None:
    with override_ui_settings(tmp_path):
        response = client.get("/services/billing-sync?environment=prod")

    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text


def test_api_404_does_not_return_spa_index(client, tmp_path) -> None:
    with override_ui_settings(tmp_path):
        response = client.get("/api/v1/definitely-missing")

    assert response.status_code == 404
    assert "text/html" not in response.headers.get("content-type", "")


def test_missing_ui_asset_returns_404(client, tmp_path) -> None:
    with override_ui_settings(tmp_path):
        response = client.get("/assets/missing.js")

    assert response.status_code == 404
