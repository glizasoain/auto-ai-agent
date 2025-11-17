"""Unit tests for the circle drawing helpers."""
import pytest

from circle_demo.draw import draw_circle, parse_color, validate_radius


def test_validate_radius_accepts_numeric():
    assert validate_radius(20) == 20
    assert validate_radius("30") == 30


def test_validate_radius_rejects_nonpositive():
    with pytest.raises(ValueError):
        validate_radius(0)
    with pytest.raises(ValueError):
        validate_radius(-5)


def test_parse_color_normalizes_lowercase():
    assert parse_color("#ABCDEF") == "#abcdef"


def test_parse_color_invalid_format():
    with pytest.raises(ValueError):
        parse_color("123456")


def test_draw_circle_creates_file(tmp_path):
    output = tmp_path / "circle.png"
    result = draw_circle(50, "#ff0000", output)
    assert result.exists()
    assert result.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
