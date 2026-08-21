"""
CoachAI – Assistant Page
==========================

Interactive AI chatbot powered by Google Gemini with function calling.
Provides data-grounded productivity coaching using the user's actual
CoachAI data (tasks, analytics, schedule, history).
"""

from __future__ import annotations

import streamlit as st

from layout import page_setup, page_title, empty_state
from services import get_current_user_id


# ─────────────────────────────────────────────────────────────
# Page Setup
# ─────────────────────────────────────────────────────────────

page_setup(title="Assistant", icon="💬")
page_title(
    "💬",
    "CoachAI Assistant",
    "Ask me anything about your productivity, tasks, schedule, and performance.",
)

user_id = get_current_user_id()


# ─────────────────────────────────────────────────────────────
# Session State Initialization
# ─────────────────────────────────────────────────────────────

if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []

if "chatbot_service" not in st.session_state:
    st.session_state.chatbot_service = None
    st.session_state.chatbot_user_id = None


# ─────────────────────────────────────────────────────────────
# API Key Check
# ─────────────────────────────────────────────────────────────

from chatbot_service import is_api_key_configured, create_chatbot_service

if not is_api_key_configured():
    empty_state(
        icon="🔑",
        title="API Key Not Configured",
        message=(
            "To use CoachAI Assistant, add your Gemini API key to the .env file: "
            "GOOGLE_API_KEY=your_api_key_here — then restart the application."
        ),
    )
    st.stop()


# ─────────────────────────────────────────────────────────────
# Service Initialization
# ─────────────────────────────────────────────────────────────

def _get_chatbot_service():
    """Get or create the chatbot service for the current user."""
    current_user = get_current_user_id()
    # Recreate if user changed or service doesn't exist
    if (
        st.session_state.chatbot_service is None
        or st.session_state.chatbot_user_id != current_user
    ):
        try:
            st.session_state.chatbot_service = create_chatbot_service(
                current_user,
            )
            st.session_state.chatbot_user_id = current_user
        except ValueError as e:
            st.error(f"⚠️ {e}")
            st.stop()
        except Exception:
            st.error(
                "⚠️ Could not initialize the AI Assistant. "
                "Please check your API key configuration."
            )
            st.stop()
    return st.session_state.chatbot_service


# ─────────────────────────────────────────────────────────────
# Header Actions
# ─────────────────────────────────────────────────────────────

col1, col2 = st.columns([4, 1])
with col2:
    if st.button("🗑️ Clear Chat", use_container_width=True):
        st.session_state.chat_messages = []
        st.session_state.chatbot_service = None
        st.session_state.chatbot_user_id = None
        st.rerun()


# ─────────────────────────────────────────────────────────────
# Welcome Message
# ─────────────────────────────────────────────────────────────

if not st.session_state.chat_messages:
    st.markdown(
        """
        <div class="insight-card">
            <p>👋 <strong>Welcome to CoachAI Assistant!</strong></p>
            <p style="margin-top:0.5rem; color:var(--text-secondary); font-size:0.9rem;">
                I'm your AI productivity coach. I can analyze your real CoachAI data
                to give you personalized insights. Try asking:
            </p>
            <ul style="color:var(--text-secondary); font-size:0.85rem; margin-top:0.3rem;">
                <li>"How was my productivity this week?"</li>
                <li>"What tasks do I have today?"</li>
                <li>"Should I add another task today?"</li>
                <li>"What time am I usually most productive?"</li>
                <li>"How long do similar tasks usually take me?"</li>
                <li>"Is my current plan realistic?"</li>
                <li>"Why did I fail more tasks yesterday?"</li>
                <li>"I'm thinking of studying for 2 hours now. What do you think?"</li>
            </ul>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────
# Chat History Display
# ─────────────────────────────────────────────────────────────

for msg in st.session_state.chat_messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


# ─────────────────────────────────────────────────────────────
# Chat Input
# ─────────────────────────────────────────────────────────────

if prompt := st.chat_input("Ask CoachAI Assistant..."):
    # Display user message immediately
    st.session_state.chat_messages.append(
        {"role": "user", "content": prompt},
    )
    with st.chat_message("user"):
        st.markdown(prompt)

    # Get AI response — streamed token by token
    with st.chat_message("assistant"):
        try:
            service = _get_chatbot_service()
            response = st.write_stream(service.send_message_stream(prompt))
            # write_stream may return a list of chunks instead of a str
            # depending on what was yielded; normalize to a single string.
            if not isinstance(response, str):
                response = "".join(str(chunk) for chunk in response)
        except Exception:
            response = (
                "⚠️ Something went wrong while processing your message. "
                "Please try again."
            )
            st.markdown(response)

    # Save assistant response to history
    st.session_state.chat_messages.append(
        {"role": "assistant", "content": response},
    )