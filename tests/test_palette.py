import os
import pty
import threading

from rich.console import Console

from bibliotheke import palette


def test_parse_osc11_takes_any_channel_width():
    assert palette.parse_osc11(b"\x1b]11;rgb:0000/0000/0000\x1b\\") == (0.0, 0.0, 0.0)
    assert palette.parse_osc11(b"\x1b]11;rgb:ff/ff/ff\x07") == (1.0, 1.0, 1.0)
    assert palette.parse_osc11(b"\x1b[?62c") is None


def test_is_light_splits_backgrounds():
    assert not palette.is_light((0, 0, 0)) and not palette.is_light((0.1, 0.1, 0.12))
    assert palette.is_light((1, 1, 1)) and palette.is_light((0.96, 0.96, 0.94))


def test_colorfgbg_reads_the_last_field():
    assert palette.colorfgbg("15;0") == "dark"
    assert palette.colorfgbg("0;default;15") == "light"
    assert palette.colorfgbg("") is None


def _terminal(leader: int, reply: bytes) -> threading.Thread:
    """Play the terminal on a pty's leader side: wait for the query, answer."""

    def serve():
        seen = b""
        while b"\x1b[c" not in seen:
            seen += os.read(leader, 256)
        os.write(leader, reply)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread


def test_query_background_reads_the_terminals_answer():
    leader, follower = pty.openpty()
    try:
        _terminal(leader, b"\x1b]11;rgb:1111/2222/3333\x1b\\\x1b[?62;c")
        assert palette.query_background(follower, timeout_s=5) == (0x1111 / 0xFFFF, 0x2222 / 0xFFFF, 0x3333 / 0xFFFF)
    finally:
        os.close(leader), os.close(follower)


def test_query_background_stops_at_da1_without_osc11():
    leader, follower = pty.openpty()
    try:
        _terminal(leader, b"\x1b[?62;c")
        assert palette.query_background(follower, timeout_s=5) is None  # returns on DA1, not the timeout
    finally:
        os.close(leader), os.close(follower)


def test_query_background_is_none_off_a_terminal(tmp_path):
    fd = os.open(tmp_path / "f", os.O_RDWR | os.O_CREAT)
    try:
        assert palette.query_background(fd) is None
    finally:
        os.close(fd)


def test_background_env_override_wins(monkeypatch):
    monkeypatch.setenv("BIB_BACKGROUND", "light")
    assert palette.background() == "light"
    monkeypatch.setenv("BIB_BACKGROUND", "nonsense")
    monkeypatch.setenv("COLORFGBG", "0;15")
    monkeypatch.setattr(palette.sys.stdin, "isatty", lambda: False)
    assert palette.background() == "light"


def test_theme_prints_hex_not_ansi_names():
    console = Console(theme=palette.theme("dark"), force_terminal=True, color_system="truecolor", record=True, file=open(os.devnull, "w"))
    console.print("[ok]Data OK[/ok] [bad]x[/bad]")
    out = console.export_text(styles=True)
    assert "38;2;34;197;94" in out and "38;2;239;68;68" in out  # #22C55E and #EF4444 as truecolor escapes
    light = palette.theme("light").styles["ok"].color.triplet.hex.upper()
    assert light == palette.LIGHT["ok"]
