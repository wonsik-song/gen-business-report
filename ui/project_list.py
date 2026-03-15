import streamlit as st
from utils.db import get_user_projects, create_project

def render_project_list():
    st.title("내 프로젝트")
    st.caption("프로젝트를 만들고 작업 공간에서 바로 기획서 생성을 시작하세요.")

    user = st.session_state.user
    if not user:
        st.error("먼저 로그인해주세요.")
        return

    with st.expander("➕ 새 프로젝트 만들기", expanded=True):
        with st.form("new_project_form"):
            p_title = st.text_input("프로젝트 제목", max_chars=100, placeholder="예: AI 기획서 평가 서비스")
            submit = st.form_submit_button("생성", type="primary", use_container_width=True)

            if submit:
                normalized_title = p_title.strip()
                if not normalized_title:
                    st.warning("프로젝트 제목을 입력해주세요.")
                else:
                    try:
                        new_proj = create_project(user.id, normalized_title)
                        st.success(f"프로젝트 '{normalized_title}'이(가) 생성되었습니다!")
                        st.session_state.current_project = new_proj
                        st.rerun()
                    except Exception as e:
                        st.error(f"프로젝트 생성 실패: {e}")

    st.divider()

    projects = get_user_projects(user.id)

    if not projects:
        st.info("프로젝트가 없습니다. 위에서 새로 만들어주세요.")
        return

    st.caption("최근 생성순으로 표시됩니다.")

    cols = st.columns(2)
    for i, proj in enumerate(projects):
        col = cols[i % 2]
        with col:
            with st.container(border=True):
                st.markdown(f"### {proj['title']}")
                st.caption(f"생성일: {proj['created_at'][:10]}")
                st.write(f"상태: **{proj['status']}**")

                if st.button("프로젝트 열기", key=f"open_{proj['id']}", use_container_width=True):
                    st.session_state.current_project = proj
                    st.session_state.current_draft = None
                    st.session_state.chat_messages = []
                    st.session_state.chat_preview_md = ""
                    st.session_state.chat_phase = "IDLE"
                    st.session_state.doc_view_mode = "preview"
                    st.session_state.latest_eval_result = None
                    st.session_state._nav_target = "작업 공간"
                    st.rerun()
