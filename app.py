# app.py
import os
from typing import Optional

import streamlit as st
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

st.set_page_config(page_title="⚖️ LexiClarus", layout="wide")

# -------------------------------
# Config
# -------------------------------
def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    return os.environ.get(key, default)


DEFAULT_BACKEND = get_env("BACKEND_URL", "http://127.0.0.1:8000")
DEFAULT_TIMEOUT = int(get_env("API_TIMEOUT", 1000))
DEFAULT_VERIFY = str(get_env("HTTPS_VERIFY", "true")).lower() != "false"

st.session_state.setdefault("backend_url", DEFAULT_BACKEND)
st.session_state.setdefault("api_timeout", DEFAULT_TIMEOUT)
st.session_state.setdefault("https_verify", DEFAULT_VERIFY)
st.session_state.setdefault("analysis", None)
st.session_state.setdefault("full_context", "")

# -------------------------------
# Sidebar
# -------------------------------
with st.sidebar:
    st.title("⚙️ Settings")

    st.session_state.backend_url = st.text_input(
        "FastAPI backend URL",
        value=st.session_state.backend_url,
        help="e.g. http://127.0.0.1:8000",
    )

    st.session_state.api_timeout = st.number_input(
        "API timeout (seconds)",
        min_value=10,
        max_value=3600,
        value=int(st.session_state.api_timeout),
        step=10,
    )

    st.session_state.https_verify = st.checkbox(
        "Verify HTTPS certificates",
        value=bool(st.session_state.https_verify),
    )

    st.markdown("---")
    if st.button("Ping backend"):
        try:
            url = f"{st.session_state.backend_url.rstrip('/')}/"
            r = requests.get(url, timeout=int(st.session_state.api_timeout), verify=st.session_state.https_verify)
            r.raise_for_status()
            st.success(f"✅ {r.json().get('message')}")
        except Exception as e:
            st.error(f"❌ Backend not reachable: {e}")

# -------------------------------
# Main UI
# -------------------------------
st.title("⚖️ LexiClarus: AI Legal Navigator")
st.markdown("Upload your rental agreement (PDF or DOCX) for clause simplification and risk analysis.")

uploaded_file = st.file_uploader("Upload a document", type=["pdf", "docx"])

col1, col2 = st.columns([3, 2])

with col1:
    if uploaded_file:
        st.info(f"📂 {uploaded_file.name} ({uploaded_file.type})")
        if st.button("🔍 Analyze Document"):
            with st.spinner("Analyzing..."):
                try:
                    files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
                    resp = requests.post(
                        f"{st.session_state.backend_url.rstrip('/')}/analyze-document/",
                        files=files,
                        timeout=int(st.session_state.api_timeout),
                        verify=st.session_state.https_verify,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    st.session_state.analysis = data
                    st.session_state.full_context = "\n\n".join(
                        [c.get("original_clause", "") for c in data.get("clauses", [])]
                    )
                    st.success("✅ Analysis complete!")
                except Exception as e:
                    st.error(f"❌ Error: {e}")
    else:
        st.info("📑 Upload a PDF or DOCX and click 'Analyze Document'.")

with col2:
    st.header("⚡ Quick Controls")
    if st.session_state.analysis:
        if st.button("🧹 Clear Results"):
            st.session_state.analysis = None
            st.session_state.full_context = ""
            st.success("Cleared.")

st.markdown("---")

# -------------------------------
# Results
# -------------------------------
if st.session_state.analysis:
    clauses = st.session_state.analysis.get("clauses", [])
    st.subheader("📜 Clause-by-Clause Analysis")
    if not clauses:
        st.warning("No clauses detected.")
    else:
        for c in clauses:
            # 🧹 Clean simplified text so it doesn't show instruction echoes
            simplified_clean = c.get("simplified_text", "").strip()
            if simplified_clean.lower().startswith("simplify this legal clause"):
                parts = simplified_clean.split("\n\n", 1)
                simplified_clean = parts[-1].strip()

            with st.expander(f"Clause {c['clause_number']}: {simplified_clean[:100]}..."):
                st.info(f"**📝 Simplified:** {simplified_clean}")

                # ⚠️ Risk handling
                risk_flags = c.get("risk_flags", [])
                if risk_flags:
                    st.markdown("**⚠️ Risks detected:**")
                    for flag in risk_flags:
                        st.warning(f"- {flag.replace('-', ' ').capitalize()}")
                else:
                    st.success("✅ No major risks.")

                st.markdown("**📜 Original Clause**")
                st.write(c.get("original_clause", ""))

    st.markdown("---")
    st.subheader("❓ Ask a Question")
    q_col1, q_col2 = st.columns([3, 1])
    with q_col1:
        question = st.text_input("Your question", placeholder="e.g. Does this contract mention penalties?")
    with q_col2:
        if st.button("Ask"):
            if question.strip():
                try:
                    resp = requests.post(
                        f"{st.session_state.backend_url.rstrip('/')}/ask-question/",
                        json={"question": question, "context": st.session_state.full_context},
                        timeout=int(st.session_state.api_timeout),
                        verify=st.session_state.https_verify,
                    )
                    resp.raise_for_status()
                    st.success("💡 Answer:")
                    st.write(resp.json().get("answer", "No answer returned."))
                except Exception as e:
                    st.error(f"❌ Error: {e}")
            else:
                st.warning("Enter a question first.")

# -------------------------------
# Footer
# -------------------------------
st.markdown("---")
st.caption("⚖️ LexiClarus — Streamlit frontend ↔ FastAPI backend. Run backend with: uvicorn main:app --reload")
