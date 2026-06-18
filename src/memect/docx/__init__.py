"""Lightweight DOCX creation SDK.

The implementation writes WordprocessingML directly and only depends on
``lxml`` plus the Python standard library.
"""

from .document import Document
from .errors import DocxError, UnsupportedImageError, ValidationError
from .model import (
    Caption,
    DocumentDefaults,
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
    TableOfContents,
    TableOfFigures,
)
from .units import Length, cm, emu, inch, mm, pt, px, twip

Docx = Document

__all__ = [
    "Document",
    "Docx",
    "DocxError",
    "Caption",
    "DocumentDefaults",
    "HeaderFooter",
    "Length",
    "Paragraph",
    "ParagraphFormat",
    "ParagraphStyle",
    "PageField",
    "PageNumbering",
    "Footnote",
    "FootnoteReference",
    "Run",
    "RunStyle",
    "Section",
    "SectionMargins",
    "Table",
    "TableCell",
    "TableRow",
    "TableOfContents",
    "TableOfFigures",
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
