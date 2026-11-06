"""按输入内容、阶段参数和模型配置区分缓存；损坏缓存不静默接受。"""

from __future__ import annotations

from pathlib import Path

from .extract import AudioError
from .storage import digest, read_json, write_json

SCHEMA = 1


class StageCache:
    def __init__(self, root: Path):
        self.root = root

    def path(self, stage: str, identity) -> Path:
        directory = self.root / stage
        if self.root.is_symlink() or directory.is_symlink():
            raise AudioError("缓存目录不能是软链接。")
        return directory / (digest({"schema": SCHEMA, "stage": stage, "identity": identity}) + ".json")

    def get(self, stage, identity):
        path = self.path(stage, identity)
        if path.is_symlink():
            raise AudioError(f"缓存文件不能是软链接：{path}")
        if not path.exists():
            return None
        envelope = read_json(path)
        if (not isinstance(envelope, dict) or envelope.get("schema") != SCHEMA or
                envelope.get("identity") != digest(identity) or "data" not in envelope or
                envelope.get("data_hash") != digest(envelope["data"])):
            raise AudioError(f"缓存损坏，请移走该文件后重试：{path}")
        return envelope["data"]

    def put(self, stage, identity, data):
        write_json(self.path(stage, identity), {
            "schema": SCHEMA, "identity": digest(identity),
            "data_hash": digest(data), "data": data,
        }, overwrite=True)
