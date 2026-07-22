import pytest

from app.core.config import Settings


STRONG_SECRETS = {
    "SECRET_KEY": "env-jwt-secret",
    "AES_SECRET_KEY": "env-aes-secret",
    "BACKUP_ENCRYPTION_KEY": "env-backup-secret",
    "INITIAL_ADMIN_PASSWORD": "env-admin-secret",
}


def missing_secret_files(tmp_path):
    return {
        "SECRET_KEY_FILE": str(tmp_path / "missing-jwt"),
        "AES_SECRET_KEY_FILE": str(tmp_path / "missing-aes"),
        "BACKUP_ENCRYPTION_KEY_FILE": str(tmp_path / "missing-backup"),
        "INITIAL_ADMIN_PASSWORD_FILE": str(tmp_path / "missing-admin"),
    }


def set_secret_environment(monkeypatch, values=STRONG_SECRETS):
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_production_uses_environment_secrets_when_files_are_absent(monkeypatch, tmp_path):
    set_secret_environment(monkeypatch)

    settings = Settings(
        _env_file=None,
        APP_ENV="production",
        **missing_secret_files(tmp_path),
    )

    for name, value in STRONG_SECRETS.items():
        assert getattr(settings, name) == value


def test_secret_file_takes_precedence_over_environment(monkeypatch, tmp_path):
    set_secret_environment(monkeypatch)
    jwt_file = tmp_path / "jwt"
    jwt_file.write_text("file-jwt-secret\n", encoding="utf-8")
    file_paths = missing_secret_files(tmp_path)
    file_paths["SECRET_KEY_FILE"] = str(jwt_file)

    settings = Settings(_env_file=None, APP_ENV="production", **file_paths)

    assert settings.SECRET_KEY == "file-jwt-secret"
    assert settings.AES_SECRET_KEY == STRONG_SECRETS["AES_SECRET_KEY"]


def test_production_rejects_missing_file_and_environment_secret(monkeypatch, tmp_path):
    for name in STRONG_SECRETS:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError) as error:
        Settings(
            _env_file=None,
            APP_ENV="production",
            **missing_secret_files(tmp_path),
        )

    assert "SECRET_KEY" in str(error.value)


def test_production_rejects_default_weak_environment_secret(monkeypatch, tmp_path):
    values = dict(STRONG_SECRETS)
    values["INITIAL_ADMIN_PASSWORD"] = "admin123"
    set_secret_environment(monkeypatch, values)

    with pytest.raises(RuntimeError) as error:
        Settings(
            _env_file=None,
            APP_ENV="production",
            **missing_secret_files(tmp_path),
        )

    assert "INITIAL_ADMIN_PASSWORD" in str(error.value)
