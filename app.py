import streamlit as st
from dotenv import load_dotenv

from utils.db import get_db
from ui.theme import apply_global_theme

# Load environment variables
load_dotenv()

# Streamlit Page Config
st.set_page_config(
    page_title="AI PRD Writer & Evaluator",
    page_icon="📝",
    layout="wide",
    initial_sidebar_state="expanded",
)

def init_session_state():
    if "user" not in st.session_state:
        st.session_state.user = None
    if "current_project" not in st.session_state:
        st.session_state.current_project = None
    if "current_draft" not in st.session_state:
        st.session_state.current_draft = None

def render_login():
    left, center, right = st.columns([1, 2.2, 1])
    with center:
        st.title("AI 기획서 작성기 로그인")
        st.caption("이메일로 로그인하고 프로젝트를 바로 시작하세요.")

        with st.container(border=True):
            with st.form("login_form"):
                email = st.text_input("이메일", placeholder="name@example.com")
                password = st.text_input("비밀번호", type="password", placeholder="비밀번호를 입력하세요")
                submitted = st.form_submit_button("로그인", use_container_width=True)

                if submitted:
                    if not email or not password:
                        st.warning("이메일과 비밀번호를 모두 입력해주세요.")
                        return
                    try:
                        db = get_db()
                        response = db.auth.sign_in_with_password({"email": email, "password": password})
                        db.postgrest.auth(response.session.access_token)
                        st.session_state.user = response.user
                        st.session_state._access_token = response.session.access_token
                        st.rerun()
                    except Exception as e:
                        st.error(f"로그인 실패: {str(e)}")

        st.markdown(
            "<p class='gb-muted'>로그인이 안 되면 환경변수(`SUPABASE_URL`, `SUPABASE_KEY`)와 계정 정보를 확인하세요.</p>",
            unsafe_allow_html=True,
        )
                
def main():
    apply_global_theme()
    init_session_state()
    
    if not st.session_state.user:
        render_login()
        return

    st.sidebar.title("탐색")
    st.sidebar.write(f"로그인: {st.session_state.user.email}")
    if st.session_state.current_project:
        st.sidebar.caption(f"현재 프로젝트: {st.session_state.current_project.get('title', '-')}")
    
    if st.sidebar.button("로그아웃", type="secondary", use_container_width=True):
        st.session_state.user = None
        get_db().auth.sign_out()
        st.rerun()
        
    st.sidebar.divider()
    
    page_map = {
        "프로젝트": "📁 프로젝트",
        "작업 공간": "🛠 작업 공간",
        "설정": "⚙️ 설정",
    }
    pages = list(page_map.keys())
    if "_nav_target" in st.session_state:
        st.session_state.nav_page = st.session_state.pop("_nav_target")
    
    page = st.sidebar.radio("이동", pages, format_func=lambda key: page_map[key], key="nav_page")
    if page == "프로젝트":
        st.sidebar.success("프로젝트를 선택하거나 새로 생성하세요.")
    elif page == "작업 공간":
        st.sidebar.info("채팅 생성 → 편집 → 평가 → 확정 순서로 진행하세요.")
    else:
        st.sidebar.info("평가 프롬프트를 저장하면 다음 평가부터 반영됩니다.")
    
    if page == "프로젝트":
        from ui.project_list import render_project_list
        render_project_list()
    elif page == "작업 공간":
        from ui.workspace import render_workspace
        render_workspace()
    elif page == "설정":
        from ui.settings import render_settings
        render_settings()

if __name__ == "__main__":
    main()
