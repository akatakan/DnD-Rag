import streamlit as st

from agent import build_engine
from config import (
    GEMINI_API_KEY,
    HYBRID_ENABLED,
    MEMORY_MESSAGE_LIMIT,
    OLLAMA_LLM_MODEL,
    RERANK_ENABLED,
    SESSION_DB,
)
from dice import DiceError, roll, roll_command
from errors import AppError, normalize_error
from game_state import Combatant
from retriever import load_book_catalog
from session_store import SessionStore
from sources import extract_sources

st.set_page_config(page_title="D&D RAG", page_icon="🎲", layout="wide")


@st.cache_resource
def get_store() -> SessionStore:
    return SessionStore(SESSION_DB)


@st.cache_resource(show_spinner="Retrieval hattı yükleniyor...")
def get_engine(
    selected_provider: str,
    books: tuple[str, ...],
    first_page: int | None,
    last_page: int | None,
    use_hybrid: bool,
    use_rerank: bool,
):
    return build_engine(
        selected_provider,
        allowed_books=books,
        page_from=first_page,
        page_to=last_page,
        hybrid_enabled=use_hybrid,
        rerank_enabled=use_rerank,
    )


def rerun() -> None:
    st.rerun()


def render_activity(activity: list[str]) -> None:
    if activity:
        with st.expander("İşlem ayrıntıları"):
            for item in activity:
                st.write(item)


def render_sources(sources: list[dict], expanded: bool = False) -> None:
    if sources:
        with st.expander("Kaynaklar", expanded=expanded):
            for source in sources:
                page = source.get("page")
                suffix = f", s. {page}" if page is not None else ""
                st.write(f"{source['book']}{suffix}")


store = get_store()
sessions = store.list_sessions()
if not sessions:
    store.create_session()
    sessions = store.list_sessions()
session_ids = [item["id"] for item in sessions]
session_titles = {item["id"]: item["title"] for item in sessions}
if st.session_state.get("session_id") not in session_ids:
    st.session_state.session_id = session_ids[0]

catalog = load_book_catalog()
book_ids = tuple(catalog)

with st.sidebar:
    st.title("D&D Asistanı")
    selected_session = st.selectbox(
        "Oturum",
        options=session_ids,
        index=session_ids.index(st.session_state.session_id),
        format_func=lambda session_id: session_titles[session_id],
    )
    if selected_session != st.session_state.session_id:
        st.session_state.session_id = selected_session
        rerun()

    session_col, delete_col = st.columns(2)
    if session_col.button("Yeni", use_container_width=True):
        st.session_state.session_id = store.create_session()
        rerun()
    if delete_col.button("Sil", use_container_width=True, disabled=len(sessions) == 1):
        store.delete_session(st.session_state.session_id)
        st.session_state.session_id = store.list_sessions()[0]["id"]
        rerun()

    st.divider()
    provider = st.radio(
        "LLM Sağlayıcı",
        options=["ollama", "gemini"],
        format_func=lambda value: "Ollama (Lokal)" if value == "ollama" else "Gemini (API)",
    )
    if provider == "ollama":
        if OLLAMA_LLM_MODEL:
            st.caption(f"Model: {OLLAMA_LLM_MODEL}")
        else:
            st.warning(".env dosyasında OLLAMA_LLM_MODEL seçin.")
    elif not GEMINI_API_KEY:
        st.error(".env dosyasına GEMINI_API_KEY ekleyin.")

    selected_books = tuple(
        st.multiselect(
            "Kitap kapsamı",
            options=book_ids,
            default=book_ids,
            format_func=lambda book_id: catalog[book_id].title,
        )
    )
    retrieval_mode = st.segmented_control(
        "Retrieval",
        options=["Dense", "Hybrid"],
        default="Hybrid" if HYBRID_ENABLED else "Dense",
    )
    rerank_enabled = st.toggle("Reranking", value=RERANK_ENABLED)
    show_activity = st.toggle("İşlem ayrıntıları", value=True)
    filter_pages = st.toggle("Sayfa filtresi", value=False)
    if filter_pages:
        page_from = int(st.number_input("İlk sayfa", min_value=1, value=1))
        page_to = int(st.number_input("Son sayfa", min_value=page_from, value=115))
    else:
        page_from = page_to = None

session_id = st.session_state.session_id
state = store.load_state(session_id)

if provider == "gemini" and not GEMINI_API_KEY:
    st.warning("Gemini API anahtarı eksik.")
    st.stop()
if not selected_books:
    st.warning("En az bir kitap seçin.")
    st.stop()

try:
    engine = get_engine(
        provider,
        selected_books,
        page_from,
        page_to,
        retrieval_mode == "Hybrid",
        rerank_enabled,
    )
except Exception as error:
    st.error(str(normalize_error(error, provider)))
    st.stop()

chat_tab, character_tab, encounter_tab, session_tab = st.tabs(
    ["Sohbet", "Karakter", "Encounter", "Oturum"]
)

with chat_tab:
    header_col, mode_col = st.columns([3, 1])
    header_col.subheader(session_titles[session_id])
    response_mode = mode_col.segmented_control(
        "Yanıt modu", options=["Kural", "Anlatım"], default="Kural"
    )

    for message in store.messages(session_id):
        display_role = "assistant" if message["role"] == "tool" else message["role"]
        with st.chat_message(display_role):
            st.write(message["content"])
            if show_activity:
                render_activity(message.get("activity", []))
            render_sources(message.get("sources", []))

    query = st.chat_input("Kural sor veya /roll 2d20kh1+5 yaz...")
    if query:
        memory = store.memory_context(session_id, MEMORY_MESSAGE_LIMIT)
        store.add_message(session_id, "user", query)
        with st.chat_message("user"):
            st.write(query)
        with st.chat_message("assistant"):
            expression = roll_command(query)
            if expression is not None:
                try:
                    answer = roll(expression).format()
                except DiceError as error:
                    answer = str(error)
                st.write(answer)
                store.add_message(session_id, "tool", answer, activity=["Dice roller çalıştırıldı"])
            else:
                activity = []
                status = st.status("Kaynaklar inceleniyor...", expanded=show_activity)

                def report_progress(stage: str, message: str) -> None:
                    activity.append(message)
                    if show_activity:
                        status.write(message)
                    labels = {
                        "routing": "Kaynak kapsamı belirleniyor...",
                        "reading": "Kural kitapları taranıyor...",
                        "reranking": "Aday kaynaklar sıralanıyor...",
                        "synthesis": "Kural cevabı hazırlanıyor...",
                        "creative": "Sahne anlatımı hazırlanıyor...",
                        "complete": "Yanıt hazır",
                    }
                    status.update(label=labels.get(stage, message))

                try:
                    response = engine.query(
                        query,
                        progress=report_progress,
                        memory_context=memory,
                        game_context=state.context(),
                        response_mode="story" if response_mode == "Anlatım" else "rules",
                    )
                    answer = str(response)
                    response_sources = extract_sources(response)
                    status.update(label="Yanıt hazır", state="complete", expanded=False)
                    st.write(answer)
                    render_sources(response_sources, expanded=True)
                    store.add_message(
                        session_id,
                        "assistant",
                        answer,
                        sources=response_sources,
                        activity=activity,
                    )
                except AppError as error:
                    status.update(label="İşlem tamamlanamadı", state="error")
                    st.error(str(error))

with character_tab:
    st.subheader("Karakter Durumu")
    character = state.character
    with st.form("character_sheet"):
        name_col, class_col, level_col = st.columns(3)
        name = name_col.text_input("Ad", value=character.name)
        character_class = class_col.text_input("Sınıf", value=character.character_class)
        level = level_col.number_input("Seviye", 1, 20, character.level)
        ac_col, max_col, current_col, temp_col = st.columns(4)
        armor_class = ac_col.number_input("AC", 0, 40, character.armor_class)
        max_hp = max_col.number_input("Maksimum HP", 1, 9999, character.max_hp)
        current_hp = current_col.number_input("Güncel HP", 0, 9999, character.current_hp)
        temporary_hp = temp_col.number_input("Geçici HP", 0, 9999, character.temporary_hp)
        if st.form_submit_button("Karakteri Kaydet"):
            character.name = name
            character.character_class = character_class
            character.level = level
            character.armor_class = armor_class
            character.max_hp = max_hp
            character.current_hp = current_hp
            character.temporary_hp = temporary_hp
            character.normalize()
            store.save_state(session_id, state)
            rerun()

    hp_amount = int(st.number_input("HP miktarı", min_value=1, value=1))
    damage_col, heal_col = st.columns(2)
    if damage_col.button("Hasar Uygula", use_container_width=True):
        character.apply_damage(hp_amount)
        store.save_state(session_id, state)
        rerun()
    if heal_col.button("İyileştir", use_container_width=True):
        character.heal(hp_amount)
        store.save_state(session_id, state)
        rerun()

    st.subheader("Envanter")
    if character.inventory:
        st.write(" · ".join(character.inventory))
    with st.form("inventory_add", clear_on_submit=True):
        new_item = st.text_input("Eşya")
        if st.form_submit_button("Ekle") and new_item.strip():
            character.inventory.append(new_item.strip())
            character.normalize()
            store.save_state(session_id, state)
            rerun()
    if character.inventory:
        remove_item = st.selectbox("Çıkarılacak eşya", character.inventory)
        if st.button("Envanterden Çıkar"):
            character.inventory.remove(remove_item)
            store.save_state(session_id, state)
            rerun()

with encounter_tab:
    encounter = state.encounter
    st.subheader("Encounter")
    current = encounter.current
    metric_cols = st.columns(3)
    metric_cols[0].metric("Durum", encounter.status)
    metric_cols[1].metric("Tur", encounter.round_number)
    metric_cols[2].metric("Sıra", current.name if current else "-")

    for index, combatant in enumerate(encounter.combatants, start=1):
        hp = f" · HP {combatant.hp}" if combatant.hp is not None else ""
        st.write(f"{index}. {combatant.name} · Initiative {combatant.initiative}{hp}")

    with st.form("combatant_add", clear_on_submit=True):
        c_name, c_init, c_hp = st.columns(3)
        combatant_name = c_name.text_input("Katılımcı")
        initiative = c_init.number_input("Initiative", -20, 100, 10)
        hp_value = c_hp.number_input("HP (0: bilinmiyor)", 0, 9999, 0)
        if st.form_submit_button("Katılımcı Ekle") and combatant_name.strip():
            encounter.combatants.append(
                Combatant(combatant_name.strip(), int(initiative), int(hp_value) or None)
            )
            store.save_state(session_id, state)
            rerun()

    start_col, next_col, complete_col, reset_col = st.columns(4)
    if start_col.button("Başlat", disabled=not encounter.combatants):
        encounter.start()
        store.save_state(session_id, state)
        rerun()
    if next_col.button("Sonraki Tur", disabled=encounter.status != "active"):
        encounter.next_turn()
        store.save_state(session_id, state)
        rerun()
    if complete_col.button("Bitir", disabled=encounter.status != "active"):
        encounter.complete()
        store.save_state(session_id, state)
        rerun()
    if reset_col.button("Sıfırla", disabled=not encounter.combatants):
        encounter.reset()
        store.save_state(session_id, state)
        rerun()

with session_tab:
    st.subheader("Oturum Hafızası")
    with st.form("session_details"):
        title = st.text_input("Oturum adı", value=session_titles[session_id])
        notes = st.text_area("Kalıcı notlar", value=state.notes, height=220)
        if st.form_submit_button("Oturumu Kaydet"):
            state.notes = notes
            store.rename_session(session_id, title)
            store.save_state(session_id, state)
            rerun()
    st.caption(f"Sohbet mesajı: {len(store.messages(session_id))}")
    if st.button("Sohbet Geçmişini Temizle"):
        store.clear_messages(session_id)
        rerun()
