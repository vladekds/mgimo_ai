"""Общие функции хуков. Хуки никогда не должны ронять работу агента: все ошибки глотаются."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
PROJECTS = Path(os.environ.get("STRATEG_PROJECTS_DIR") or ROOT / "projects")


def read_input() -> dict:
    try:
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8")
        return json.load(sys.stdin)
    except Exception:
        return {}


def active_project_dir():
    slug = os.environ.get("STRATEG_PROJECT")
    f = PROJECTS / ".active"
    if not slug and f.exists():
        slug = f.read_text(encoding="utf-8").strip()
    if slug and (PROJECTS / slug).is_dir():
        return PROJECTS / slug
    return None


def emit(obj: dict):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(obj, ensure_ascii=False))
