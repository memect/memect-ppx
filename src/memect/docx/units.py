"""Unit conversion helpers for WordprocessingML."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EMU_PER_INCH = 914400
TWIPS_PER_INCH = 1440
TWIPS_PER_POINT = 20
POINTS_PER_INCH = 72
EMU_PER_TWIP = EMU_PER_INCH / TWIPS_PER_INCH

Unit = Literal["in", "cm", "mm", "pt", "px", "twip", "emu"]


@dataclass(frozen=True)
class Length:
    """A physical length value.

    Numeric values passed directly to public APIs are interpreted by that API.
    Use these helpers when precision matters: ``inch(1)``, ``cm(2.5)``,
    ``pt(12)``, or ``px(640)``.
    """

    value: float
    unit: Unit
    dpi: float = 96

    def inches(self) -> float:
        if self.unit == "in":
            return self.value
        if self.unit == "cm":
            return self.value / 2.54
        if self.unit == "mm":
            return self.value / 25.4
        if self.unit == "pt":
            return self.value / POINTS_PER_INCH
        if self.unit == "px":
            return self.value / self.dpi
        if self.unit == "twip":
            return self.value / TWIPS_PER_INCH
        if self.unit == "emu":
            return self.value / EMU_PER_INCH
        raise ValueError(f"Unsupported unit: {self.unit}")

    def points(self) -> float:
        return self.inches() * POINTS_PER_INCH

    def twips(self) -> int:
        if self.unit == "twip":
            return int(round(self.value))
        return int(round(self.inches() * TWIPS_PER_INCH))

    def emu(self) -> int:
        if self.unit == "emu":
            return int(round(self.value))
        return int(round(self.inches() * EMU_PER_INCH))

    def half_points(self) -> int:
        return int(round(self.points() * 2))


def inch(value: float) -> Length:
    return Length(float(value), "in")


def cm(value: float) -> Length:
    return Length(float(value), "cm")


def mm(value: float) -> Length:
    return Length(float(value), "mm")


def pt(value: float) -> Length:
    return Length(float(value), "pt")


def px(value: float, *, dpi: float = 96) -> Length:
    return Length(float(value), "px", dpi=dpi)


def twip(value: float) -> Length:
    return Length(float(value), "twip")


def emu(value: float) -> Length:
    return Length(float(value), "emu")


def ensure_length(
    value: Length | int | float | None,
    *,
    default_unit: Unit,
    dpi: float = 96,
) -> Length | None:
    if value is None:
        return None
    if isinstance(value, Length):
        return value
    return Length(float(value), default_unit, dpi=dpi)


def to_twips(value: Length | int | float | None, *, default_unit: Unit) -> int | None:
    length = ensure_length(value, default_unit=default_unit)
    return None if length is None else length.twips()


def to_emu(value: Length | int | float | None, *, default_unit: Unit) -> int | None:
    length = ensure_length(value, default_unit=default_unit)
    return None if length is None else length.emu()


def to_half_points(value: Length | int | float | None) -> int | None:
    length = ensure_length(value, default_unit="pt")
    return None if length is None else length.half_points()
