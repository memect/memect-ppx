"""Lightweight PPTX creation SDK.

The implementation writes PresentationML directly and only depends on
``lxml`` plus the Python standard library.
"""

from .errors import PptxError, UnsupportedImageError, ValidationError
from .model import (
    Alignment,
    Line,
    Paragraph,
    Picture,
    Shape,
    Slide,
    Table,
    TableCell,
    TextBox,
    TextStyle,
    ThemeDefaults,
    VerticalAlignment,
)
from .presentation import Presentation, Pptx
from .units import Length, cm, emu, inch, mm, pt, px

__all__ = [
    "Alignment",
    "Length",
    "Line",
    "Paragraph",
    "Picture",
    "Pptx",
    "PptxError",
    "Presentation",
    "Shape",
    "Slide",
    "Table",
    "TableCell",
    "TextBox",
    "TextStyle",
    "ThemeDefaults",
    "UnsupportedImageError",
    "ValidationError",
    "VerticalAlignment",
    "cm",
    "emu",
    "inch",
    "mm",
    "pt",
    "px",
]
