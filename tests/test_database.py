from discord_tts.storage.database import Database


def test_quick_chat_defaults_and_history(tmp_path):
    db = Database(tmp_path / "history.sqlite3")
    quick = db.quick_messages()
    assert len(quick) == 4
    assert all(item["model_id"] == "vi_VN-vais1000-medium" for item in quick)
    hello = next(item for item in quick if item["title"].lower() == "hello")
    assert hello["text"] == "Xin chào mọi người!"
    assert hello["speaker_id"] == 0
    assert hello["speed"] == 1.0
    assert hello["volume"] == 1.0

    row_id = db.add_history("outgoing", "app", "vi", "Xin chào")
    assert row_id > 0
    history = db.recent_history()
    assert history[-1]["text"] == "Xin chào"
    assert history[-1]["language"] == "vi"
