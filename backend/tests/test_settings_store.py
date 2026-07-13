from settings_store import SettingsStore


def test_get_returns_defaults_when_no_row_exists_yet():
    store = SettingsStore()
    settings = store.get()
    assert settings["max_concurrent_downloads"] == 3
    assert settings["concurrent_fragment_downloads"] == 8
    assert settings["aria2c_enabled"] is True
    assert settings["skip_duplicates"] is False
    assert settings["default_output_folder"] is None
    assert settings["gemini_api_key"] is None
    assert settings["anthropic_api_key"] is None
    assert settings["openai_api_key"] is None
    assert settings["groq_api_key"] is None
    assert settings["openrouter_api_key"] is None
    assert settings["transcription_provider"] == "gemini"
    assert settings["summarization_provider"] == "anthropic"
    assert settings["auto_transcribe_on_download"] is False
    assert settings["auto_summarize_after_transcribe"] is False


def test_update_persists_auto_process_toggles():
    store = SettingsStore()
    updated = store.update(
        auto_transcribe_on_download=True, auto_summarize_after_transcribe=True
    )
    assert updated["auto_transcribe_on_download"] is True
    assert updated["auto_summarize_after_transcribe"] is True
    # Round-trips through a fresh get() as real booleans, not 0/1 ints.
    reread = store.get()
    assert reread["auto_transcribe_on_download"] is True
    assert reread["auto_summarize_after_transcribe"] is True


def test_update_can_turn_auto_process_back_off():
    store = SettingsStore()
    store.update(auto_transcribe_on_download=True)
    store.update(auto_transcribe_on_download=False)
    assert store.get()["auto_transcribe_on_download"] is False


def test_update_persists_api_keys():
    store = SettingsStore()
    updated = store.update(
        gemini_api_key="sk-gemini-abc", anthropic_api_key="sk-ant-xyz",
        openai_api_key="sk-openai-abc", groq_api_key="gsk-abc",
        openrouter_api_key="sk-or-abc",
    )
    assert updated["gemini_api_key"] == "sk-gemini-abc"
    assert updated["anthropic_api_key"] == "sk-ant-xyz"
    assert updated["openai_api_key"] == "sk-openai-abc"
    assert updated["groq_api_key"] == "gsk-abc"
    assert updated["openrouter_api_key"] == "sk-or-abc"


def test_update_persists_provider_selection():
    store = SettingsStore()
    updated = store.update(transcription_provider="groq", summarization_provider="openrouter")
    assert updated["transcription_provider"] == "groq"
    assert updated["summarization_provider"] == "openrouter"


def test_update_persists_partial_changes():
    store = SettingsStore()
    updated = store.update(max_concurrent_downloads=5)
    assert updated["max_concurrent_downloads"] == 5
    assert updated["concurrent_fragment_downloads"] == 8


def test_update_only_changes_provided_fields():
    store = SettingsStore()
    store.update(aria2c_enabled=False)
    settings = store.get()
    assert settings["aria2c_enabled"] is False
    assert settings["max_concurrent_downloads"] == 3


def test_update_ignores_none_values():
    store = SettingsStore()
    store.update(max_concurrent_downloads=5)
    store.update(max_concurrent_downloads=None)
    assert store.get()["max_concurrent_downloads"] == 5


def test_settings_persist_across_store_instances_pointing_at_same_file(tmp_path):
    db_path = tmp_path / "settings.db"
    store1 = SettingsStore(db_path)
    store1.update(max_concurrent_downloads=9)

    store2 = SettingsStore(db_path)
    assert store2.get()["max_concurrent_downloads"] == 9
