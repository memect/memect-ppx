"""Public PPTX SDK entry point."""

from __future__ import annotations

from pathlib import Path

from .media import load_image
from .model import Deck, PresentationProperties, Slide, ThemeDefaults
from .units import Length


class Presentation:
    """Create a PPTX deck from a structured Python object model."""

    def __init__(
        self,
        *,
        title: str = "",
        creator: str = "memect.pptx",
        width: Length | int | float | None = None,
        height: Length | int | float | None = None,
    ) -> None:
        self._deck = Deck()
        self._deck.properties = PresentationProperties(title=title, creator=creator)
        if width is not None:
            self._deck.width = width
        if height is not None:
            self._deck.height = height

    @property
    def slides(self) -> list[Slide]:
        return self._deck.slides

    @property
    def defaults(self) -> ThemeDefaults:
        return self._deck.defaults

    @property
    def properties(self) -> PresentationProperties:
        return self._deck.properties

    def set_size(self, *, width: Length | int | float, height: Length | int | float) -> Presentation:
        self._deck.width = width
        self._deck.height = height
        return self

    def set_default_font(
        self,
        *,
        font: str | None = None,
        east_asia_font: str | None = None,
        size: Length | int | float | None = None,
        color: str | None = None,
    ) -> Presentation:
        if font is not None:
            self._deck.defaults.font = font
        if east_asia_font is not None:
            self._deck.defaults.east_asia_font = east_asia_font
        if size is not None:
            self._deck.defaults.size = size
        if color is not None:
            self._deck.defaults.color = color
        return self

    def add_slide(self, *, background: str | None = None) -> Slide:
        return self._deck.add_slide(background=background)

    def create_picture(
        self,
        source: str | Path | bytes,
        *,
        width: Length | int | float | None = None,
        height: Length | int | float | None = None,
    ):
        return load_image(source, image_id=self._deck.next_image_id(), width=width, height=height)

    def to_bytes(self) -> bytes:
        from .ooxml import build_package

        return build_package(self._deck)

    def save(self, path: str | Path) -> None:
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(self.to_bytes())


Pptx = Presentation
