"""Unit conversion helpers for PresentationML."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EMU_PER_INCH = 914400
POINTS_PER_INCH = 72

Unit = Literal["in", "cm", "mm", "pt", "px", "emu"]


@dataclass(frozen=True)
class Length:
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
        if self.unit == "emu":
            return self.value / EMU_PER_INCH
        raise ValueError(f"Unsupported unit: {self.unit}")

    def points(self) -> float:
        return self.inches() * POINTS_PER_INCH

    def emu(self) -> int:
        if self.unit == "emu":
            return int(round(self.value))
        return int(round(self.inches() * EMU_PER_INCH))


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


def emu(value: float) -> Length:
    return Length(float(value), "emu")


def ensure_length(value: Length | int | float | None, *, default_unit: Unit = "pt") -> Length | None:
    if value is None:
        return None
    if isinstance(value, Length):
        return value
    return Length(float(value), default_unit)


def to_emu(value: Length | int | float | None, *, default_unit: Unit = "pt") -> int | None:
    length = ensure_length(value, default_unit=default_unit)
    return None if length is None else length.emu()


def to_points(value: Length | int | float | None, *, default_unit: Unit = "pt") -> float | None:
    length = ensure_length(value, default_unit=default_unit)
    return None if length is None else length.points()
