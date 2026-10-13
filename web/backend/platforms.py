"""Host-only operations. Importing this module never opens a key store or process."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Protocol


class SecretStore(Protocol):
    def get_password(self, service: str, account: str) -> str | None: ...
    def set_password(self, service: str, account: str, password: str) -> None: ...
    def delete_password(self, service: str, account: str) -> None: ...


class HostPlatform(Protocol):
    name: str
    def executable(self, folder: Path, name: str) -> Path: ...
    def process_options(self) -> dict: ...
    def library_environment(self, folders: list[Path]) -> dict[str, str]: ...
    def reveal(self, path: Path) -> None: ...
    def credentials(self) -> SecretStore: ...
    def capabilities(self) -> dict: ...


class PosixHost:
    name = "linux"
    library_variable = "LD_LIBRARY_PATH"
    path_separator = ":"

    def executable(self, folder, name):
        return Path(folder) / name

    def process_options(self):
        return {}

    def library_environment(self, folders):
        env = os.environ.copy()
        values = [str(p) for p in folders if p.is_dir()]
        if env.get(self.library_variable):
            values.append(env[self.library_variable])
        if values:
            env[self.library_variable] = self.path_separator.join(values)
        return env

    def reveal(self, path):
        raise RuntimeError("이 운영체제에서 파일 위치 열기는 지원하지 않습니다.")

    def credentials(self):
        # Fail closed: keyring's automatic discovery may select plaintext storage.
        raise RuntimeError("이 운영체제의 보안 자격 증명 저장소가 구성되지 않았습니다.")

    def capabilities(self):
        return {"platform": self.name, "reveal_file": False,
                "credential_store": None, "vision_ocr": False}


class MacHost(PosixHost):
    name = "macos"
    library_variable = "DYLD_LIBRARY_PATH"

    def reveal(self, path):
        subprocess.Popen(["/usr/bin/open", "-R", str(path)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def credentials(self):
        from keyring.backends.macOS import Keyring
        return Keyring()

    def capabilities(self):
        return {"platform": self.name, "reveal_file": True,
                "credential_store": "Keychain", "vision_ocr": True}


class WindowsHost(PosixHost):
    name = "windows"
    library_variable = "PATH"
    path_separator = ";"

    def executable(self, folder, name):
        folder = Path(folder)
        filename = name if name.lower().endswith(".exe") else name + ".exe"
        candidates = (folder / filename, folder / "Release" / filename)
        return next((p for p in candidates if p.is_file()), candidates[0])

    def process_options(self):
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW

    def reveal(self, path):
        subprocess.Popen(["explorer.exe", "/select,", str(path)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         **self.process_options())

    def credentials(self):
        from keyring.backends.Windows import WinVaultKeyring
        return WinVaultKeyring()

    def capabilities(self):
        return {"platform": self.name, "reveal_file": True,
                "credential_store": "Windows Credential Manager", "vision_ocr": False}


def select_host(platform: str | None = None) -> HostPlatform:
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        return MacHost()
    if platform == "win32":
        return WindowsHost()
    return PosixHost()


host = select_host()
