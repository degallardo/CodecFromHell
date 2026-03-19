import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as file_handle:
        return json.load(file_handle)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_path(path_text: str) -> str:
    return str(Path(path_text).resolve())


def read_manifest_csv(manifest_path: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with open(manifest_path, "r", encoding="utf-8-sig", newline="") as file_handle:
        reader = csv.DictReader(file_handle)
        required = {"source_path"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing required CSV columns: {sorted(missing)}")
        for row in reader:
            source_path = (row.get("source_path") or "").strip()
            if not source_path:
                continue
            rows.append(
                {
                    "source_path": normalize_path(source_path),
                    "source_file_id": (row.get("source_file_id") or "").strip(),
                    "source_sha256": (row.get("source_sha256") or "").strip(),
                    "source_size": (row.get("source_size") or "").strip(),
                    "output_file_id": (row.get("output_file_id") or "").strip(),
                }
            )
    return rows


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, rows: Iterable[Dict], fieldnames: List[str]) -> None:
    ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8", newline="") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def run_template_command(command_template: str, values: Dict[str, str]):
    import subprocess

    command_text = command_template.format(**values)
    result = subprocess.run(command_text, shell=True, capture_output=True, text=True)
    return {
        "returncode": result.returncode,
        "stdout": (result.stdout or "").strip(),
        "stderr": (result.stderr or "").strip(),
        "command": command_text,
    }


def relative_or_name(source_path: Path, source_root: Path) -> Path:
    try:
        return source_path.relative_to(source_root)
    except ValueError:
        return Path(source_path.name)
