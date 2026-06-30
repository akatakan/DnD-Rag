import streamlit as st
from agent import build_engine
from config import GEMINI_API_KEY

st.set_page_config(page_title="D&D RAG", page_icon="🎲", layout="centered")


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Ayarlar")
    st.divider()

    provider = st.radio(
        "LLM Sağlayıcı",
        options=["ollama", "gemini"],
        format_func=lambda x: "🖥️ Ollama (Lokal)" if x == "ollama" else "✨ Gemini (API)",
        index=0,
    )

    if provider == "ollama":
        st.info("**Model:** llama3.2:3b\n\nOllama'nın çalıştığından emin ol.", icon="🖥️")
    else:
        if GEMINI_API_KEY:
            st.success("API anahtarı bulundu.", icon="✅")
        else:
            st.error("`.env` dosyasına `GEMINI_API_KEY` ekle.", icon="🔑")

    st.divider()
    if st.button("🗑️ Sohbeti Temizle", use_container_width=True):
        st.session_state.messages = []
        st.rerun()


# ── Engine ────────────────────────────────────────────────────────────────────
if provider == "gemini" and not GEMINI_API_KEY:
    st.warning("Gemini API anahtarı eksik. Sidebar'dan Ollama'ya geç veya `.env` dosyasına `GEMINI_API_KEY` ekle.")
    st.stop()


@st.cache_resource(show_spinner="Araçlar yükleniyor...")
def get_engine(prov: str):
    return build_engine(prov)


engine = get_engine(provider)


# ── Chat UI ───────────────────────────────────────────────────────────────────
st.title("🎲 D&D Kural Asistanı")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    st.chat_message(msg["role"]).write(msg["content"])

query = st.chat_input("Sorunuzu yazın...")

if query:
    st.chat_message("user").write(query)
    st.session_state.messages.append({"role": "user", "content": query})

    with st.chat_message("assistant"):
        with st.spinner("Kitaplara bakılıyor..."):
            response = engine.query(query)
            answer = str(response)

        st.write(answer)
        st.session_state.messages.append({"role": "assistant", "content": answer})
