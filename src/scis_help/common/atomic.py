"""Atomic file writes: write a temporary file next to the target, then ``os.replace``."""

import json
import os
from pathlib import Path


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(text, encoding="utf-8")
    os.replace(temp_path, path)


def write_json_atomic(path: Path, data: object) -> None:
    write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False))
