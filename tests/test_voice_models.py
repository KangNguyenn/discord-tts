import json

from discord_tts import config as config_module
from discord_tts.config import AppConfig
from discord_tts.tts.engine import PiperEngine


def _write_voice(folder, name, metadata):
    model_path = folder / f"{name}.onnx"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_bytes(b"test model placeholder")
    model_path.with_suffix(".onnx.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    return model_path


def test_discovers_local_model_and_reads_metadata(tmp_path):
    voice_dir = tmp_path / "voices"
    _write_voice(
        voice_dir / "vi" / "vi_VN" / "vais1000" / "medium",
        "vi_VN-vais1000-medium",
        {
            "audio": {"sample_rate": 22050, "quality": "medium"},
            "language": {
                "code": "vi_VN",
                "name_native": "Tiếng Việt",
                "name_english": "Vietnamese",
            },
            "num_speakers": 2,
            "speaker_id_map": {"speaker_a": 0, "speaker_b": 1},
        },
    )

    models = PiperEngine(voice_dir).list_models()

    assert len(models) == 1
    model = models[0]
    assert model.display_name == "vi_VN-vais1000-medium"
    assert model.model_id == "vi/vi_VN/vais1000/medium/vi_VN-vais1000-medium"
    assert model.language_code == "vi_VN"
    assert model.language_name == "Vietnamese"
    assert model.quality == "medium"
    assert model.sample_rate == 22050
    assert model.num_speakers == 2
    assert model.speakers == (("speaker_a", 0), ("speaker_b", 1))


def test_get_model_accepts_legacy_bare_filename(tmp_path):
    voice_dir = tmp_path / "voices"
    _write_voice(
        voice_dir / "nested",
        "en_US-lessac-medium",
        {
            "audio": {"sample_rate": 22050},
            "espeak": {"voice": "en-us"},
            "num_speakers": 1,
        },
    )

    engine = PiperEngine(voice_dir)
    model = engine.get_model("en_US-lessac-medium")

    assert model is not None
    assert model.model_id == "nested/en_US-lessac-medium"
    assert engine.language_code(model.model_id) == "en_us"


def test_ignores_models_without_matching_metadata(tmp_path):
    voice_dir = tmp_path / "voices"
    voice_dir.mkdir()
    (voice_dir / "missing-sidecar.onnx").write_bytes(b"placeholder")
    (voice_dir / "invalid-json.onnx").write_bytes(b"placeholder")
    (voice_dir / "invalid-json.onnx.json").write_text("{", encoding="utf-8")

    assert PiperEngine(voice_dir).list_models() == []


def test_multi_speaker_map_fills_unnamed_ids(tmp_path):
    voice_dir = tmp_path / "voices"
    _write_voice(
        voice_dir,
        "en_US-sample-medium",
        {
            "audio": {"sample_rate": 22050},
            "language": {"code": "en_US", "name_english": "English"},
            "num_speakers": 3,
            "speaker_id_map": {"first": 0, "third": 2},
        },
    )

    model = PiperEngine(voice_dir).list_models()[0]

    assert model.speakers == (
        ("first", 0),
        ("Speaker 1", 1),
        ("third", 2),
    )


def test_old_config_migrates_discord_language_to_model(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({
            "discord_incoming_language": "en",
            "voice_en": "custom-English-model",
        }),
        encoding="utf-8",
    )

    config = AppConfig.load()

    assert config.discord_voice_model == "custom-English-model"
    assert config.discord_speaker_id == 0
    assert config.app_voice_model == "vi_VN-vais1000-medium"
