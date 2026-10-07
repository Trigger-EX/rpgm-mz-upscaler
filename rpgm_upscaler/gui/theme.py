"""Colours that follow the user's Qt palette instead of fixed hex values (dark themes, high contrast)."""
from __future__ import annotations

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QWidget


def is_dark(widget: QWidget) -> bool:
    return widget.palette().color(QPalette.Window).lightness() < 128


def error_color(widget: QWidget) -> str:
    """A red that stays readable on the current background."""
    return "#ff7b7b" if is_dark(widget) else "#b00020"
