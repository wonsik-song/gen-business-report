import streamlit as st
from utils.db import get_global_prompts, get_project_prompt, get_db
from agents.prompts import (
    get_agent_prompt_templates,
    apply_agent_prompt_templates,
    reset_agent_prompt_templates,
)
from utils.prompt_store import (
    load_user_agent_prompt_templates,
    save_user_agent_prompt_templates,
    clear_user_agent_prompt_templates,
)

def render_settings():
    st.title("설정")
    st.caption("에이전트 모델과 평가 프롬프트를 관리합니다.")

    user = st.session_state.user
    if not user:
        st.error("먼저 로그인해주세요.")
        return

    tab_models, tab_prompts, tab_global, tab_project = st.tabs(["에이전트 모델", "에이전트 프롬프트", "글로벌", "프로젝트별"])

    with tab_models:
        st.subheader("에이전트별 모델 선택")
        st.caption("선택 즉시 다음 요청부터 반영됩니다.")

        model_options = [
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.5-flash-lite",
            "gemini-3.1-pro-preview",
            "gemini-3-flash-preview",
            "gemini-3.1-flash-lite-preview",
        ]

        if "planner_model_name" not in st.session_state:
            st.session_state.planner_model_name = "gemini-3.1-pro-preview"
        if "evaluator_model_name" not in st.session_state:
            st.session_state.evaluator_model_name = "gemini-3.1-pro-preview"
        if "host_model_name" not in st.session_state:
            st.session_state.host_model_name = "gemini-3.1-pro-preview"

        planner_idx = model_options.index(st.session_state.planner_model_name) if st.session_state.planner_model_name in model_options else 3
        evaluator_idx = model_options.index(st.session_state.evaluator_model_name) if st.session_state.evaluator_model_name in model_options else 3
        host_idx = model_options.index(st.session_state.host_model_name) if st.session_state.host_model_name in model_options else 3

        selected_planner = st.selectbox(
            "Planner Agent 모델",
            model_options,
            index=planner_idx,
            key="planner_model_selectbox",
        )
        selected_evaluator = st.selectbox(
            "Evaluator Agent 모델",
            model_options,
            index=evaluator_idx,
            key="evaluator_model_selectbox",
        )
        selected_host = st.selectbox(
            "Host Agent 모델",
            model_options,
            index=host_idx,
            key="host_model_selectbox",
        )

        if st.button("모델 설정 저장", use_container_width=True):
            st.session_state.planner_model_name = selected_planner
            st.session_state.evaluator_model_name = selected_evaluator
            st.session_state.host_model_name = selected_host
            # Force host agent refresh in workspace.
            st.session_state.host_agent_signature = ""
            st.success("모델 설정을 저장했습니다. 다음 요청부터 새 모델이 적용됩니다.")

    with tab_prompts:
        st.subheader("에이전트 프롬프트 편집")
        st.caption("Host/Planner/Evaluator의 시스템 프롬프트를 확인/수정하고 사용자별로 영구 저장합니다.")

        current_templates = get_agent_prompt_templates()
        current_user_id = str(user.id)
        saved_user_id = st.session_state.get("agent_prompt_templates_user_id")
        if "agent_prompt_templates" not in st.session_state or saved_user_id != current_user_id:
            persisted_templates = load_user_agent_prompt_templates(current_user_id)
            if persisted_templates:
                current_templates = apply_agent_prompt_templates(persisted_templates)
            st.session_state.agent_prompt_templates = dict(current_templates)
            st.session_state.agent_prompt_templates_user_id = current_user_id

        templates = st.session_state.agent_prompt_templates

        host_prompt = st.text_area(
            "Host 프롬프트",
            value=templates.get("host_orchestration_system", ""),
            height=120,
            key="host_prompt_input",
        )
        planner_outline_prompt = st.text_area(
            "Planner 프롬프트 (OUTLINE)",
            value=templates.get("planner_outline_system", ""),
            height=120,
            key="planner_outline_prompt_input",
        )
        planner_full_prompt = st.text_area(
            "Planner 프롬프트 (FULL_DOCUMENT)",
            value=templates.get("planner_full_doc_system", ""),
            height=120,
            key="planner_full_prompt_input",
        )
        planner_revise_prompt = st.text_area(
            "Planner 프롬프트 (REVISE)",
            value=templates.get("planner_revise_system", ""),
            height=120,
            key="planner_revise_prompt_input",
        )
        evaluator_prompt = st.text_area(
            "Evaluator 프롬프트",
            value=templates.get("evaluator_system", ""),
            height=120,
            key="evaluator_prompt_input",
        )

        cols = st.columns(2)
        if cols[0].button("에이전트 프롬프트 저장", use_container_width=True):
            overrides = {
                "host_orchestration_system": host_prompt,
                "planner_outline_system": planner_outline_prompt,
                "planner_full_doc_system": planner_full_prompt,
                "planner_revise_system": planner_revise_prompt,
                "evaluator_system": evaluator_prompt,
            }
            applied = apply_agent_prompt_templates(overrides)
            st.session_state.agent_prompt_templates = dict(applied)
            st.session_state.agent_prompt_templates_user_id = current_user_id
            save_user_agent_prompt_templates(current_user_id, dict(applied))
            st.session_state.host_agent_signature = ""
            st.success("에이전트 프롬프트를 저장했습니다. 다음 요청부터 반영되며 재접속 후에도 유지됩니다.")

        if cols[1].button("기본 프롬프트 복원", use_container_width=True):
            restored = reset_agent_prompt_templates()
            st.session_state.agent_prompt_templates = dict(restored)
            st.session_state.agent_prompt_templates_user_id = current_user_id
            clear_user_agent_prompt_templates(current_user_id)
            st.session_state.host_agent_signature = ""
            st.success("기본 프롬프트로 복원했습니다. 사용자 저장값도 초기화되었습니다.")
            st.rerun()

    with tab_global:
        st.subheader("글로벌 기본 프롬프트")
        global_prompts = get_global_prompts(user.id)
        current_global = global_prompts[0]["custom_rules"] if global_prompts else ""
        new_global = st.text_area(
            "글로벌 평가 기준",
            value=current_global,
            height=200,
            key="global_prompt_input",
            placeholder="모든 프로젝트에 공통으로 적용할 평가 기준을 입력하세요.",
        )

        if st.button("글로벌 프롬프트 저장", use_container_width=True):
            db = get_db()
            if global_prompts:
                db.table("evaluation_prompts").update({"custom_rules": new_global}).eq("id", global_prompts[0]["id"]).execute()
            else:
                db.table("evaluation_prompts").insert(
                    {
                        "user_id": user.id,
                        "custom_rules": new_global,
                    }
                ).execute()
            st.success("글로벌 프롬프트가 저장되었습니다.")

    with tab_project:
        st.subheader("프로젝트별 프롬프트")
        proj = st.session_state.current_project

        if not proj:
            st.info("프로젝트별 프롬프트를 설정하려면 먼저 프로젝트를 선택하세요.")
            return

        st.caption(f"현재 선택 프로젝트: {proj['title']}")
        proj_prompt = get_project_prompt(proj["id"])
        current_proj = proj_prompt["custom_rules"] if proj_prompt else ""
        new_proj = st.text_area(
            "프로젝트 평가 기준",
            value=current_proj,
            height=200,
            key="proj_prompt_input",
            placeholder="이 프로젝트에만 적용할 상세 평가 기준을 입력하세요.",
        )

        if st.button("프로젝트 프롬프트 저장", use_container_width=True):
            db = get_db()
            if proj_prompt:
                db.table("evaluation_prompts").update({"custom_rules": new_proj}).eq("id", proj_prompt["id"]).execute()
            else:
                db.table("evaluation_prompts").insert(
                    {
                        "user_id": user.id,
                        "project_id": proj["id"],
                        "custom_rules": new_proj,
                    }
                ).execute()
            st.success(f"'{proj['title']}' 프로젝트 프롬프트가 저장되었습니다.")
