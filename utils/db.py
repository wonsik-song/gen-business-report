import os
import streamlit as st
from supabase import create_client, Client

def get_db() -> Client:
    if "_supabase" not in st.session_state:
        url: str = os.environ.get("SUPABASE_URL")
        key: str = os.environ.get("SUPABASE_KEY")
        if not url or not key:
            raise ValueError("Supabase URL and Key must be set in environment variables.")
        st.session_state._supabase = create_client(url, key)
    client = st.session_state._supabase
    token = st.session_state.get("_access_token")
    if token:
        client.postgrest.auth(token)
    return client

# --- DB Helpers ---

def get_user_projects(user_id: str):
    db = get_db()
    res = db.table("projects").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
    return res.data

def create_project(user_id: str, title: str):
    db = get_db()
    res = db.table("projects").insert({
        "user_id": user_id,
        "title": title
    }).execute()
    return res.data[0] if res.data else None

def get_document_draft(project_id: str):
    db = get_db()
    res = db.table("documents").select("*").eq("project_id", project_id).eq("is_draft", True).execute()
    return res.data[0] if res.data else None

def get_finalized_documents(project_id: str):
    db = get_db()
    res = db.table("documents").select("*").eq("project_id", project_id).eq("is_draft", False).order("version_num", desc=True).execute()
    return res.data

def upsert_draft(project_id: str, content_md: str):
    db = get_db()
    res = db.rpc("upsert_document_draft", {
        "p_project_id": project_id,
        "p_content_md": content_md
    }).execute()
    return res.data

def finalize_document(project_id: str):
    db = get_db()
    res = db.rpc("finalize_version", {
        "p_project_id": project_id
    }).execute()
    return res.data

def get_evaluations(document_id: str):
    db = get_db()
    res = db.table("evaluations").select("*").eq("document_id", document_id).order("created_at", desc=True).execute()
    return res.data

def _normalize_evaluation_status(raw_status) -> str:
    allowed = {"SUCCESS", "PARTIAL_ERROR", "FAILED"}
    status = str(raw_status or "").strip().upper()
    if status in allowed:
        return status
    if "PARTIAL" in status:
        return "PARTIAL_ERROR"
    if "SUCCESS" in status:
        return "SUCCESS"
    return "FAILED"

def save_evaluation(document_id: str, eval_data: dict):
    db = get_db()
    normalized_status = _normalize_evaluation_status(eval_data.get("status"))
    
    # Map the detailed evaluation output to the strict db schema (feedback_json)
    db_row = {
        "document_id": document_id,
        "status": normalized_status,
        "total_score": eval_data.get("total_score"),
        "prompt_tokens": eval_data.get("prompt_tokens", 0),
        "comp_tokens": eval_data.get("comp_tokens", 0),
        "feedback_json": {
            "raw_status": eval_data.get("status"),
            "category_scores": eval_data.get("category_scores"),
            "summary": eval_data.get("summary"),
            "strengths": eval_data.get("strengths", []),
            "missing_points": eval_data.get("missing_points", []),
            "raw_text": eval_data.get("raw_text")
        }
    }
    
    res = db.table("evaluations").insert(db_row).execute()
    return res.data[0] if res.data else None

def get_project_prompt(project_id: str):
    db = get_db()
    res = db.table("evaluation_prompts").select("*").eq("project_id", project_id).execute()
    return res.data[0] if res.data else None

def get_global_prompts(user_id: str):
    db = get_db()
    res = db.table("evaluation_prompts").select("*").eq("user_id", user_id).is_("project_id", "null").execute()
    return res.data

def save_audit_log(user_id: str, action_type: str, severity: str, details: dict = None):
    db = get_db()
    data = {
        "user_id": user_id,
        "action_type": action_type,
        "severity": severity
    }
    if details:
        data["details"] = details
    db.table("audit_logs").insert(data).execute()

def get_daily_generate_count(user_id: str) -> int:
    db = get_db()
    try:
        res = db.rpc("get_daily_generate_count", {"p_user_id": user_id}).execute()
        val = res.data
        if isinstance(val, int):
            return val
        if isinstance(val, list) and val:
            return int(val[0]) if not isinstance(val[0], dict) else 0
        return 0
    except Exception:
        return 0

def get_daily_evaluate_count(user_id: str) -> int:
    db = get_db()
    try:
        res = db.rpc("get_daily_evaluate_count", {"p_user_id": user_id}).execute()
        val = res.data
        if isinstance(val, int):
            return val
        if isinstance(val, list) and val:
            return int(val[0]) if not isinstance(val[0], dict) else 0
        return 0
    except Exception:
        return 0
