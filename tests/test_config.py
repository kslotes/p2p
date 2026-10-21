from pathlib import Path

import pytest

from p2p.config import (
    DEFAULT_DB_PATH,
    Credentials,
    credentials_from,
    db_path_from,
    load_env,
    parse_env,
)


def write_env(tmp_path: Path, text: str, encoding: str = "utf-8") -> Path:
    path = tmp_path / ".env"
    path.write_text(text, encoding=encoding)
    return path


def test_parse_env_ignores_blanks_comments_and_lines_without_equals():
    text = "\n# a comment\n   \n  # indented comment\nNOT_A_PAIR\nKEY=value\n"

    assert parse_env(text) == {"KEY": "value"}


def test_parse_env_strips_whitespace_and_matching_quotes():
    text = (
        "PLAIN = spaced value \n"
        'DOUBLE="double quoted"\n'
        "SINGLE='single quoted'\n"
        "MISMATCHED=\"open'\n"
        "EQUALS=a=b=c\n"
        "export EXPORTED=yes\n"
        "EMPTY=\n"
    )

    assert parse_env(text) == {
        "PLAIN": "spaced value",
        "DOUBLE": "double quoted",
        "SINGLE": "single quoted",
        "MISMATCHED": "\"open'",
        "EQUALS": "a=b=c",
        "EXPORTED": "yes",
        "EMPTY": "",
    }


def test_load_env_tolerates_a_utf8_bom(tmp_path):
    path = write_env(tmp_path, "BINANCE_API_KEY=key\n", encoding="utf-8-sig")

    assert load_env(path, environ={}) == {"BINANCE_API_KEY": "key"}


def test_load_env_without_a_file_uses_only_the_environment(tmp_path):
    env = load_env(tmp_path / "missing.env", environ={"BINANCE_API_KEY": "from-env"})

    assert env == {"BINANCE_API_KEY": "from-env"}


def test_real_environment_variables_win_over_the_file(tmp_path):
    path = write_env(
        tmp_path, "BINANCE_API_KEY=file-key\nBINANCE_API_SECRET=file-secret\n"
    )

    env = load_env(path, environ={"BINANCE_API_KEY": "env-key"})

    assert credentials_from(env) == Credentials("env-key", "file-secret")


def test_an_empty_environment_variable_does_not_hide_the_file_value(tmp_path):
    path = write_env(tmp_path, "BINANCE_API_KEY=file-key\n")

    assert load_env(path, environ={"BINANCE_API_KEY": ""}) == {
        "BINANCE_API_KEY": "file-key"
    }


def test_load_env_defaults_to_the_current_working_directory(tmp_path, monkeypatch):
    write_env(tmp_path, "BINANCE_API_KEY=cwd-key\n")
    monkeypatch.chdir(tmp_path)

    assert load_env(environ={})["BINANCE_API_KEY"] == "cwd-key"


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"BINANCE_API_KEY": "key"},
        {"BINANCE_API_SECRET": "secret"},
        {"BINANCE_API_KEY": "key", "BINANCE_API_SECRET": "  "},
    ],
)
def test_credentials_are_none_when_either_value_is_missing(env):
    assert credentials_from(env) is None


def test_credentials_never_reveal_the_key_or_secret():
    credentials = Credentials("my-api-key-123", "my-secret-456")

    for text in (repr(credentials), str(credentials), f"{credentials}"):
        assert "my-secret-456" not in text
        assert "my-api-key-123" not in text
    assert credentials.api_key == "my-api-key-123"
    assert credentials.api_secret == "my-secret-456"


def test_db_path_precedence_flag_then_env_then_default():
    env = {"P2P_DB_PATH": "env/orders.sqlite"}

    assert DEFAULT_DB_PATH == Path("data/p2p.sqlite")
    assert db_path_from({}) == DEFAULT_DB_PATH
    assert db_path_from(env) == Path("env/orders.sqlite")
    assert db_path_from(env, "flag.sqlite") == Path("flag.sqlite")
