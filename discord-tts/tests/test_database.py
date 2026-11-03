from discord_tts.storage.database import Database


def test_quick_chat_defaults_and_history(tmp_path):
    db = Database(tmp_path / "history.sqlite3")
    quick = db.quick_messages()
    assert len(quick) >= 4
    assert db.find_quick_message("hello")["text"] == "Hello everyone!"

    row_id = db.add_history("outgoing", "app", "vi", "Xin chào")
    assert row_id > 0
    history = db.recent_history()
    assert history[-1]["text"] == "Xin chào"
    assert history[-1]["language"] == "vi"
