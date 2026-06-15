"""Exceptions raised by the DOCX SDK."""


class DocxError(Exception):
    """Base error for document generation failures."""


class ValidationError(DocxError):
    """Raised when the document model is internally inconsistent."""


class UnsupportedImageError(DocxError):
    """Raised when an image cannot be embedded in a DOCX file."""
