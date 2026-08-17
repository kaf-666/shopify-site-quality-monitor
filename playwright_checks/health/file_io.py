import json
import os
import uuid
from pathlib import Path


def atomic_write_text(path, content):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".staging-{uuid.uuid4().hex}{target.suffix}"
    try:
        staging.write_text(content, encoding="utf-8")
        os.replace(staging, target)
    finally:
        try:
            staging.unlink(missing_ok=True)
        except TypeError:
            if staging.exists():
                staging.unlink()


def atomic_write_json(path, payload):
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, text)
