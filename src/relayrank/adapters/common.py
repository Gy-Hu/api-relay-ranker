from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit


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


def safe_http_url(value: object, origin_only: bool = False) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(str(value).strip())
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return None
    path = "/" if origin_only else parsed.path
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def item_list_details(html: str, name_contains: str | None = None) -> list[tuple[int, str, str | None]]:
    candidates: list[list[tuple[int, str, str | None]]] = []
    for document in json_ld_documents(html):
        for node in walk_json(document):
            if node.get("@type") != "ItemList":
                continue
            if name_contains and name_contains.casefold() not in str(node.get("name", "")).casefold():
                continue
            parsed: list[tuple[int, str, str | None]] = []
            for entry in node.get("itemListElement", []):
                if not isinstance(entry, dict):
                    continue
                item = entry.get("item")
                name = item.get("name") if isinstance(item, dict) else entry.get("name")
                position = entry.get("position")
                if name and position is not None:
                    provider = item.get("provider") if isinstance(item, dict) else None
                    provider_url = provider.get("url") if isinstance(provider, dict) else None
                    item_url = item.get("url") if isinstance(item, dict) else None
                    parsed.append(
                        (int(position), str(name).strip(), safe_http_url(provider_url or item_url))
                    )
            if parsed:
                candidates.append(parsed)
    if not candidates:
        raise ValueError("no JSON-LD ItemList found")
    return max(candidates, key=len)


def item_list(html: str, name_contains: str | None = None) -> list[tuple[int, str]]:
    return [(position, name) for position, name, _ in item_list_details(html, name_contains)]


def escaped_number(block: str, name: str) -> float | None:
    match = re.search(rf'\\"{re.escape(name)}\\":(-?[0-9]+(?:\.[0-9]+)?)', block)
    return float(match.group(1)) if match else None


class Node:
    def __init__(self, tag: str, attrs=(), parent=None):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children: list[Node | str] = []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def has_class(self, name: str) -> bool:
        return name in (self.attrs.get("class") or "").split()

    def text(self) -> str:
        if self.tag in {"script", "style"}:
            return ""
        return " ".join(c if isinstance(c, str) else c.text() for c in self.children)


class Document(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.current = self.root
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in self.VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def next_flight(html: str) -> str:
    """Decode escaped script strings before reading bounded JSON objects."""
    parts = []
    for match in re.finditer(r'self\.__next_f\.push\((.*?)\)</script>', html, re.S):
        try:
            payload = json.loads(match[1])
            if len(payload) > 1 and payload[0] == 1 and isinstance(payload[1], str):
                parts.append(payload[1])
        except (ValueError, TypeError):
            continue
    return "".join(parts)


def flight_records(flight: str, key: str, required: str) -> list[dict[str, Any]]:
    """The single JSON array of objects stored under `key` whose objects carry `required`."""
    decoder = json.JSONDecoder()
    found = []
    for match in re.finditer(rf'"{re.escape(key)}"\s*:\s*', flight):
        try:
            value, _ = decoder.raw_decode(flight, match.end())
        except ValueError:
            continue
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value) and required in value[0]:
            found.append(value)
    if len(found) != 1:
        raise ValueError(f"expected one embedded {key!r} array, found {len(found)}")
    return found[0]


def source_date(text: str):
    from datetime import date
    match = re.search(r'(?:数据更新于|最近更新)\s*(\d{4}-\d{2}-\d{2})', text)
    return date.fromisoformat(match[1]) if match else None
