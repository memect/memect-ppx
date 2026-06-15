"""Lightweight DOCX creation SDK.

The implementation writes WordprocessingML directly and only depends on
``lxml`` plus the Python standard library.
"""

from .document import Document
from .errors import DocxError, UnsupportedImageError, ValidationError
from .model import (
    HeaderFooter,
    Paragraph,
    ParagraphFormat,
    ParagraphStyle,
    PageField,
    PageNumbering,
    Run,
    RunStyle,
    Footnote,
    FootnoteReference,
    Section,
    SectionMargins,
    Table,
    TableCell,
    TableRow,
)
from .units import Length, cm, emu, inch, mm, pt, px, twip

Docx = Document

__all__ = [
    "Document",
    "Docx",
    "DocxError",
    "HeaderFooter",
    "Length",
    "Paragraph",
    "ParagraphFormat",
    "ParagraphStyle",
    "PageField",
    "PageNumbering",
    "Cell",
    "Footnote",
    "FootnoteReference",
    "Run",
    "RunStyle",
    "Section",
    "SectionMargins",
    "Table",
    "TableCell",
    "TableRow",
    "UnsupportedImageError",
    "ValidationError",
    "cm",
    "emu",
    "inch",
    "mm",
    "pt",
    "px",
    "twip",
]
