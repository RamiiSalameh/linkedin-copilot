from pathlib import Path

from linkedin_copilot.config import build_settings


def test_build_settings_loads_defaults(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "settings.yaml").write_text("browser: {}\nlogging: {}\nsearch_defaults: {}\n", "utf-8")

    settings = build_settings(cfg_dir)
    assert settings.env.ollama_base_url.startswith("http")
    assert isinstance(settings.browser, dict)

