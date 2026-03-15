from typing import Any, Dict, Optional


class DraftSaveTool:
    """Tool for persisting agent-generated markdown into current project draft."""

    def __init__(self):
        self.project_id: Optional[str] = None
        self.user_id: Optional[str] = None

    def configure(self, project_id: Optional[str], user_id: Optional[str] = None) -> None:
        self.project_id = project_id
        self.user_id = user_id

    def save(self, content_md: str, source: str = "planner") -> bool:
        if not self.project_id or not isinstance(content_md, str) or not content_md.strip():
            return False
        try:
            # Keep tool independent from UI modules unless actually invoked.
            from utils.db import upsert_draft, save_audit_log

            upsert_draft(self.project_id, content_md)
            if self.user_id:
                save_audit_log(
                    self.user_id,
                    "SAVE_DRAFT_BY_AGENT",
                    "INFO",
                    {"project_id": self.project_id, "source": source},
                )
            return True
        except Exception:
            return False

    def finalize_version(self, source: str = "host") -> bool:
        if not self.project_id:
            return False
        try:
            from utils.db import finalize_document, save_audit_log

            finalize_document(self.project_id)
            if self.user_id:
                save_audit_log(
                    self.user_id,
                    "FINALIZE_VERSION_BY_AGENT",
                    "INFO",
                    {"project_id": self.project_id, "source": source},
                )
            return True
        except Exception:
            return False

    def load_current_draft(self) -> Dict[str, Any]:
        if not self.project_id:
            return {"status": "FAILED", "reason": "PROJECT_NOT_CONFIGURED"}
        try:
            from utils.db import get_document_draft

            draft = get_document_draft(self.project_id)
            if not draft:
                return {"status": "NOT_FOUND", "reason": "DRAFT_NOT_FOUND"}
            return {
                "status": "SUCCESS",
                "document_id": draft.get("id"),
                "version_num": draft.get("version_num"),
                "is_draft": True,
                "content_md": draft.get("content_md", "") or "",
            }
        except Exception as e:
            return {"status": "FAILED", "reason": f"LOAD_DRAFT_ERROR:{e}"}

    def load_finalized_document(self, version_num: Optional[int] = None) -> Dict[str, Any]:
        if not self.project_id:
            return {"status": "FAILED", "reason": "PROJECT_NOT_CONFIGURED"}
        try:
            from utils.db import get_finalized_documents

            finalized_docs = get_finalized_documents(self.project_id) or []
            if not finalized_docs:
                return {"status": "NOT_FOUND", "reason": "FINALIZED_NOT_FOUND"}

            selected = None
            if isinstance(version_num, int):
                for doc in finalized_docs:
                    if doc.get("version_num") == version_num:
                        selected = doc
                        break
                if selected is None:
                    return {"status": "NOT_FOUND", "reason": f"VERSION_NOT_FOUND:{version_num}"}
            else:
                # finalized_docs is already ordered desc by version_num.
                selected = finalized_docs[0]

            return {
                "status": "SUCCESS",
                "document_id": selected.get("id"),
                "version_num": selected.get("version_num"),
                "is_draft": False,
                "content_md": selected.get("content_md", "") or "",
            }
        except Exception as e:
            return {"status": "FAILED", "reason": f"LOAD_FINALIZED_ERROR:{e}"}
