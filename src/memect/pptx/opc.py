"""Open Packaging Convention helpers for PPTX files."""

from __future__ import annotations

import io
import posixpath
import zipfile
from dataclasses import dataclass, field

from lxml import etree

CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


@dataclass
class Relationship:
    rel_id: str
    rel_type: str
    target: str
    target_mode: str | None = None


@dataclass
class RelationshipSet:
    relationships: list[Relationship] = field(default_factory=list)

    def add(self, rel_type: str, target: str, *, target_mode: str | None = None) -> str:
        rel_id = f"rId{len(self.relationships) + 1}"
        self.relationships.append(Relationship(rel_id, rel_type, target, target_mode))
        return rel_id

    def xml(self) -> bytes:
        root = etree.Element(_q(REL_NS, "Relationships"), nsmap={None: REL_NS})
        for rel in self.relationships:
            attrs = {"Id": rel.rel_id, "Type": rel.rel_type, "Target": rel.target}
            if rel.target_mode:
                attrs["TargetMode"] = rel.target_mode
            etree.SubElement(root, _q(REL_NS, "Relationship"), attrs)
        return etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)


@dataclass
class Part:
    name: str
    content_type: str
    data: bytes


class Package:
    def __init__(self) -> None:
        self.parts: dict[str, Part] = {}
        self.rels: dict[str, RelationshipSet] = {}
        self.defaults: dict[str, str] = {
            "rels": "application/vnd.openxmlformats-package.relationships+xml",
            "xml": "application/xml",
        }

    def add_default(self, extension: str, content_type: str) -> None:
        self.defaults[extension.lower().lstrip(".")] = content_type

    def add_part(self, name: str, content_type: str, data: bytes) -> None:
        name = name.lstrip("/")
        self.parts[name] = Part(name, content_type, data)

    def relationships(self, source: str = "") -> RelationshipSet:
        source = source.lstrip("/")
        if source not in self.rels:
            self.rels[source] = RelationshipSet()
        return self.rels[source]

    def to_bytes(self) -> bytes:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", self._content_types_xml())
            for source, rels in sorted(self.rels.items()):
                if rels.relationships:
                    archive.writestr(_rels_path(source), rels.xml())
            for name, part in sorted(self.parts.items()):
                archive.writestr(name, part.data)
        return out.getvalue()

    def _content_types_xml(self) -> bytes:
        root = etree.Element(_q(CONTENT_TYPES_NS, "Types"), nsmap={None: CONTENT_TYPES_NS})
        for extension, content_type in sorted(self.defaults.items()):
            etree.SubElement(
                root,
                _q(CONTENT_TYPES_NS, "Default"),
                {"Extension": extension, "ContentType": content_type},
            )
        for part in sorted(self.parts.values(), key=lambda item: item.name):
            etree.SubElement(
                root,
                _q(CONTENT_TYPES_NS, "Override"),
                {"PartName": f"/{part.name}", "ContentType": part.content_type},
            )
        return etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)


def _rels_path(source: str) -> str:
    if not source:
        return "_rels/.rels"
    directory = posixpath.dirname(source)
    filename = posixpath.basename(source)
    if directory:
        return f"{directory}/_rels/{filename}.rels"
    return f"_rels/{filename}.rels"
