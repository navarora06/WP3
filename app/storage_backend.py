import os
from dataclasses import dataclass
from typing import BinaryIO

@dataclass(frozen=True)
class StorageBackend:
    root: str  # e.g. /app/storage

    def resolve_path(self, key: str) -> str:
        key = (key or "").lstrip("/").replace("..", "")
        return os.path.join(self.root, key)

    def save_upload(self, key: str, file_storage) -> str:
        path = self.resolve_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        file_storage.save(path)
        return path

    def save_text(self, key: str, text: str) -> str:
        path = self.resolve_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def open(self, key: str, mode: str = "rb") -> BinaryIO:
        return open(self.resolve_path(key), mode)
