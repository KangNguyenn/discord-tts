from __future__ import annotations


class SecretStoreError(RuntimeError):
    pass


class SecretStore:
    """Store the Discord token in the desktop Secret Service, never in config.json."""

    SERVICE = "io.github.kang.discordtts"
    USERNAME = "discord-bot-token"

    def __init__(self) -> None:
        try:
            import keyring
            from keyring.backends.SecretService import Keyring as SecretServiceKeyring

            backend = SecretServiceKeyring()
            if backend.priority <= 0:
                raise RuntimeError("Secret Service backend is unavailable")
            keyring.set_keyring(backend)
            self._keyring = keyring
        except Exception as exc:  # DBus service may be missing on minimal WMs
            raise SecretStoreError(
                "Không tìm thấy Secret Service. Hãy cài và khởi chạy GNOME Keyring "
                "hoặc một dịch vụ Secret Service tương thích, rồi mở lại app."
            ) from exc

    def get_token(self) -> str | None:
        try:
            return self._keyring.get_password(self.SERVICE, self.USERNAME)
        except Exception as exc:
            raise SecretStoreError(f"Không đọc được Bot Token: {exc}") from exc

    def set_token(self, token: str) -> None:
        token = token.strip()
        if not token:
            raise SecretStoreError("Bot Token không được để trống.")
        try:
            self._keyring.set_password(self.SERVICE, self.USERNAME, token)
        except Exception as exc:
            raise SecretStoreError(f"Không lưu được Bot Token: {exc}") from exc

    def delete_token(self) -> None:
        try:
            self._keyring.delete_password(self.SERVICE, self.USERNAME)
        except self._keyring.errors.PasswordDeleteError:
            pass
        except Exception as exc:
            raise SecretStoreError(f"Không xóa được Bot Token: {exc}") from exc
