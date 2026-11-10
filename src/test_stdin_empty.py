import argparse
import sys
from io import BytesIO

import pytest

import img2pdf


def test_input_images_empty_stdin(monkeypatch):
    class _Stdin:
        buffer = BytesIO(b"")

    monkeypatch.setattr(sys, "stdin", _Stdin())
    with pytest.raises(argparse.ArgumentTypeError):
        img2pdf.input_images("-")
