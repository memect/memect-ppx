"""Errors raised by the lightweight PPTX SDK."""

from __future__ import annotations


class PptxError(Exception):
    """Base class for PPTX SDK errors."""


class ValidationError(PptxError, ValueError):
    """Raised when SDK input cannot produce a valid PPTX file."""


class UnsupportedImageError(PptxError):
    """Raised when an image type is not supported."""
