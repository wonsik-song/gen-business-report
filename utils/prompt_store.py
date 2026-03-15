import json
from pathlib import Path
from typing import Dict


_STORE_PATH = Path(".data/agent_prompt_settings.json")


def _read_store() -> Dict[str, dict]:
    if not _STORE_PATH.exists():
        return {}
    try:
        return json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_store(data: Dict[str, dict]) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STORE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_user_agent_prompt_templates(user_id: str) -> dict:
    if not user_id:
        return {}
    data = _read_store()
    value = data.get(str(user_id), {})
    return value if isinstance(value, dict) else {}


def save_user_agent_prompt_templates(user_id: str, templates: dict) -> None:
    if not user_id or not isinstance(templates, dict):
        return
    data = _read_store()
    data[str(user_id)] = templates
    _write_store(data)


def clear_user_agent_prompt_templates(user_id: str) -> None:
    if not user_id:
        return
    data = _read_store()
    if str(user_id) in data:
        del data[str(user_id)]
        _write_store(data)
