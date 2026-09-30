from __future__ import annotations

from urllib.parse import parse_qs, urlsplit
from ..models import Observation
from .common import Document, item_list_details, json_ld_documents, walk_json, source_date

SOURCE = "apiranking"


def parse_apiranking(body: bytes, aliases: dict[str, str]) -> list[Observation]:
    html = body.decode("utf-8", errors="strict")
    doc = Document(html).root
    lists = [n for d in json_ld_documents(html) for n in walk_json(d) if n.get("@type") == "ItemList"]
    if not lists:
        raise ValueError("APIRanking ItemList unavailable")
    declared = int(max(lists, key=lambda n: int(n.get("numberOfItems", 0))).get("numberOfItems", 0))
    # JSON-LD only carries a preview. Preserve verified URLs there, but read all cards.
    urls = {name: url for _, name, url in item_list_details(html)}
    stamp = source_date(doc.text())
    rows = {}
    for node in doc.walk():
        if node.tag != "a" or not node.has_class("provider-link"):
            continue
        href = node.attrs.get("href", "")
        if not urlsplit(href).path.startswith("/go/"):
            continue
        query = parse_qs(urlsplit(href).query)
        if "rank" not in query:
            continue
        rank = int(query["rank"][0])
        name = next((n.text().strip() for n in node.walk() if n.has_class("provider-name")), "")
        if not name or rank in rows:
            raise ValueError("APIRanking missing name or duplicate position")
        card = node.parent
        while card and not (card.has_class("card") or card.has_class("prow")):
            card = card.parent
        if card is None:
            raise ValueError(f"APIRanking missing status container for {name}")
        nodes = list(card.walk())
        inactive = any(n.has_class("closed") for n in nodes)
        flags = ["ordering_only"]
        flags += ["source_status: " + n.text().strip() for n in nodes if n.has_class("tag-anomaly")]
        if any(n.has_class("visit-btn") and n.has_class("disabled") for n in nodes):
            flags.append("source_reports_website_unavailable")
        rows[rank] = Observation(
            SOURCE, aliases.get(name.casefold(), name), rank, declared,
            website_url=urls.get(name), evidence_kind="ordering",
            state="inactive" if inactive else "reference", observed_at=stamp,
            date_basis="source_page_update" if stamp else "unknown", issues=tuple(flags),
            raw_evidence={"listing_url": "https://apiranking.com" + urlsplit(href).path,
                          "status_text": [n.text().strip() for n in nodes if n.has_class("tag-anomaly")]},
        )
    if declared < 10 or set(rows) != set(range(1, declared + 1)):
        raise ValueError(f"APIRanking incomplete list: {len(rows)}/{declared}; refusing truncated ranking")
    return [rows[i] for i in sorted(rows)]
