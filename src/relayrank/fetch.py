from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


USER_AGENT = "Mozilla/5.0 (compatible; API-Relay-Ranker/0.2; +local-research)"


@dataclass(frozen=True)
class FetchResult:
    source: str
    url: str
    fetched_at: datetime
    body: bytes
    content_type: str
    sha256: str


def fetch(source: str, url: str, timeout: float = 30, retries: int = 2) -> FetchResult:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json"})
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                return FetchResult(
                    source=source,
                    url=response.geturl(),
                    fetched_at=datetime.now(timezone.utc),
                    body=body,
                    content_type=response.headers.get_content_type(),
                    sha256=hashlib.sha256(body).hexdigest(),
                )
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt < retries:
                time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"failed to fetch {source} from {url}: {last_error}")


def save_snapshot(result: FetchResult, root: str | Path, run_id: str | None = None) -> tuple[Path, Path]:
    stamp = run_id or result.fetched_at.strftime("%Y%m%dT%H%M%SZ")
    directory = Path(root) / stamp
    directory.mkdir(parents=True, exist_ok=True)
    suffix = ".json" if "json" in result.content_type else ".html"
    body_path = directory / f"{result.source}{suffix}"
    metadata_path = directory / f"{result.source}.meta.json"
    body_path.write_bytes(result.body)
    metadata = {
        "source": result.source,
        "url": result.url,
        "fetched_at": result.fetched_at.isoformat(),
        "content_type": result.content_type,
        "bytes": len(result.body),
        "sha256": result.sha256,
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return body_path, metadata_path
