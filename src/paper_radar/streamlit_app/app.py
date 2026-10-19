"""Robotics Paper Radar: the Streamlit script. Start it with the `paper-radar` command."""

import streamlit as st

from paper_radar.config import ASSETS_DIR
from paper_radar.ui import feed, library, queue, settings, theme
from paper_radar.ui.context import get_context

st.set_page_config(page_title="Paper Radar", page_icon=str(ASSETS_DIR / "mark.svg"), layout="centered")

# Keep filter values when switching pages (Streamlit drops state of widgets that aren't drawn).
for key in [k for k in st.session_state if k.startswith(("feed_", "lib_"))]:
    st.session_state[key] = st.session_state[key]

try:
    get_context()
except Exception as exc:  # configuration or database problems: explain instead of a stack trace
    st.error(f"Paper Radar couldn't start: {exc}")
    st.stop()

theme.apply()
st.logo(str(ASSETS_DIR / "mark.svg"), size="medium")
navigation = st.navigation(
    [
        st.Page(feed.page, title="Feed", icon=":material/dynamic_feed:", url_path="feed", default=True),
        st.Page(queue.page, title="This week", icon=":material/event_note:", url_path="week"),
        st.Page(library.page, title="Library", icon=":material/library_books:", url_path="library"),
        st.Page(settings.page, title="Settings", icon=":material/tune:", url_path="settings"),
    ],
    position="top",
)
navigation.run()
