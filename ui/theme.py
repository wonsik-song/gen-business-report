import streamlit as st


def apply_global_theme() -> None:
    """Apply shared UI styles for a cleaner, user-friendly layout."""
    st.markdown(
        """
        <style>
        :root {
            --gb-border: rgba(148, 163, 184, 0.18);
            --gb-text-muted: #94a3b8;
            --gb-panel-bg: rgba(15, 23, 42, 0.32);
            --gb-accent: #22c55e;
        }
        .stApp {
            background:
                radial-gradient(1100px 500px at 85% -10%, rgba(34, 197, 94, 0.08), transparent 65%),
                radial-gradient(900px 450px at -10% 110%, rgba(59, 130, 246, 0.10), transparent 60%);
        }
        .block-container {
            padding-top: 1.25rem;
            padding-bottom: 2rem;
            max-width: 1400px;
        }
        .stApp h1, .stApp h2, .stApp h3 {
            letter-spacing: -0.01em;
            line-height: 1.2;
        }
        [data-testid="stSidebar"] {
            border-right: 1px solid var(--gb-border);
        }
        .gb-muted {
            color: var(--gb-text-muted);
            font-size: 0.92rem;
        }
        .gb-section-title {
            margin-bottom: 0.25rem;
        }
        [data-testid="stVerticalBlock"] [data-testid="stMetric"] {
            border: 1px solid var(--gb-border);
            border-radius: 12px;
            padding: 0.4rem 0.65rem;
            background: var(--gb-panel-bg);
        }
        [data-testid="stExpander"] details {
            border: 1px solid var(--gb-border);
            border-radius: 12px;
            background: var(--gb-panel-bg);
        }
        [data-testid="stChatMessage"] {
            border: 1px solid var(--gb-border);
            border-radius: 12px;
            padding: 1rem;
            background: var(--gb-panel-bg);
            margin-bottom: 0.8rem;
        }
        .stButton>button {
            border-radius: 10px;
            min-height: 2.25rem;
            border: 1px solid var(--gb-border);
            transition: transform .08s ease, border-color .12s ease;
        }
        .stButton>button:hover {
            transform: translateY(-1px);
            border-color: rgba(34, 197, 94, 0.55);
        }
        .stButton>button[kind="primary"] {
            background-color: var(--gb-accent);
            color: #062b13;
            font-weight: 700;
            border-color: rgba(34, 197, 94, 0.85);
        }
        .stButton>button[kind="primary"]:hover {
            background-color: #16a34a;
            color: #eafff1;
        }
        .stTextInput>div>div>input,
        .stTextArea textarea {
            border-radius: 10px;
            border: 1px solid var(--gb-border) !important;
        }
        [data-baseweb="base-input"] {
            background: rgba(15, 23, 42, 0.18);
        }
        .gb-toolbar {
            margin: 0.25rem 0 0.75rem 0;
        }
        .gb-host-step-row {
            display: flex;
            gap: 0.5rem;
            flex-wrap: wrap;
            margin: 0.15rem 0 0.4rem 0;
        }
        .gb-host-step-badge {
            display: inline-block;
            border: 1px solid var(--gb-border);
            border-radius: 999px;
            padding: 0.2rem 0.65rem;
            background: rgba(30, 41, 59, 0.42);
            font-size: 0.82rem;
            color: #cbd5e1;
        }
        [data-testid="stTabs"] [role="tablist"] {
            gap: 0.3rem;
            margin-top: 0.4rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
