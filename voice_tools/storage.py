"""原子文件写入；配置只保存环境变量名称，不保存密钥值。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .extract import AudioError


def json_default(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      default=json_default)


def digest(value) -> str:
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def file_hash(path: Path) -> str:
    result = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                result.update(block)
    except OSError as exc:
        raise AudioError(f"无法读取文件：{path}: {exc}") from exc
    return result.hexdigest()


def publish(temporary: Path, output: Path, overwrite: bool) -> Path:
    try:
        if output.is_symlink():
            raise AudioError(f"拒绝写入软链接：{output}")
        if overwrite:
            os.replace(temporary, output)
        else:
            os.link(temporary, output)
        return output
    except OSError as exc:
        raise AudioError(f"无法发布文件 {output}：{exc}") from exc


def write_text(path: Path, text: str, *, overwrite: bool = False) -> Path:
    temporary = None
    try:
        path = path.expanduser().absolute()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".voice-tools-", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        return publish(temporary, path, overwrite)
    except OSError as exc:
        raise AudioError(f"无法写入 {path}：{exc}") from exc
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def write_json(path: Path, value, *, overwrite: bool = False) -> Path:
    return write_text(path, dumps(value) + "\n", overwrite=overwrite)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"),
                          parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (OSError, ValueError) as exc:
        raise AudioError(f"无法读取有效 JSON {path}：{exc}") from exc
