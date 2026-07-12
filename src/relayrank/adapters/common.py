from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Iterable


class JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.documents: list[Any] = []
        self._inside = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "script" and attributes.get("type") == "application/ld+json":
            self._inside = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._inside:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "script" or not self._inside:
            return
        self._inside = False
        try:
            self.documents.append(json.loads("".join(self._parts)))
        except json.JSONDecodeError:
            pass


def json_ld_documents(html: str) -> list[Any]:
    parser = JsonLdParser()
    parser.feed(html)
    return parser.documents


def walk_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def item_list(html: str, name_contains: str | None = None) -> list[tuple[int, str]]:
    candidates: list[list[tuple[int, str]]] = []
    for document in json_ld_documents(html):
        for node in walk_json(document):
            if node.get("@type") != "ItemList":
                continue
            if name_contains and name_contains.casefold() not in str(node.get("name", "")).casefold():
                continue
            parsed: list[tuple[int, str]] = []
            for entry in node.get("itemListElement", []):
                if not isinstance(entry, dict):
                    continue
                item = entry.get("item")
                name = item.get("name") if isinstance(item, dict) else entry.get("name")
                position = entry.get("position")
                if name and position is not None:
                    parsed.append((int(position), str(name).strip()))
            if parsed:
                candidates.append(parsed)
    if not candidates:
        raise ValueError("no JSON-LD ItemList found")
    return max(candidates, key=len)


def escaped_number(block: str, name: str) -> float | None:
    match = re.search(rf'\\"{re.escape(name)}\\":(-?[0-9]+(?:\.[0-9]+)?)', block)
    return float(match.group(1)) if match else None
