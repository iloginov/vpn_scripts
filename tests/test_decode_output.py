"""Имена выходных decode-файлов."""

from __future__ import annotations

from pathlib import Path

from amnezia_export.decode_output import (
    common_stem_prefix,
    decoded_output_path,
    strip_awg_qr_stem,
    strip_qr_stem,
)


def test_strip_qr_suffix():
    assert strip_qr_stem("test2_qr") == "test2"
    assert strip_qr_stem("test2_qr2") == "test2"
    assert strip_qr_stem("qr") == "qr"


def test_common_prefix_empty():
    assert common_stem_prefix(["foo", "bar"]) == ""


def test_decoded_qr_name():
    inputs = [Path("x/test2_qr.png"), Path("x/test2_qr2.png")]
    out = decoded_output_path(
        inputs,
        strip_stem=strip_qr_stem,
        name_suffix=".qr.decoded",
    )
    assert out == Path("x/test2.qr.decoded")


def test_decoded_awg_qr_name():
    out = decoded_output_path(
        [Path("test2_awg_qr.png")],
        strip_stem=strip_awg_qr_stem,
        name_suffix=".awg_qr.decoded",
    )
    assert out == Path("test2.awg_qr.decoded")


def test_decoded_empty_prefix():
    out = decoded_output_path(
        [Path("a/foo_qr.png"), Path("b/bar_qr.png")],
        strip_stem=strip_qr_stem,
        name_suffix=".qr.decoded",
    )
    assert out.name == "output.qr.decoded"


def test_decoded_custom_output():
    out = decoded_output_path(
        [Path("any.png")],
        strip_stem=strip_qr_stem,
        name_suffix=".qr.decoded",
        output=Path("/tmp/custom.bin"),
    )
    assert out == Path("/tmp/custom.bin")
