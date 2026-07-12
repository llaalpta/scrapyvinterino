from __future__ import annotations

import codecs
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser

from curl_cffi.curl import CURL_WRITEFUNC_ERROR

from vinted_monitor.core.text import matched_exclusion_terms


@dataclass(frozen=True)
class ItemHeadSnapshot:
    canonical_url: str | None
    title: str
    description: str
    complete: bool
    bytes_observed: int

    def isolated_description(self, catalog_title: str) -> str | None:
        title = catalog_title.strip()
        prefix = f"{title} - "
        if not title or not self.description.startswith(prefix):
            return None
        return self.description[len(prefix) :].strip()


class ItemHeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.canonical_url: str | None = None
        self.description = ""
        self.title_parts: list[str] = []
        self.in_title = False
        self.complete = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.casefold(): value for name, value in attrs}
        normalized_tag = tag.casefold()
        if normalized_tag == "title":
            self.in_title = True
        elif normalized_tag == "link" and (attributes.get("rel") or "").casefold() == "canonical":
            self.canonical_url = attributes.get("href")
        elif normalized_tag == "meta" and (attributes.get("name") or "").casefold() == "description":
            self.description = (attributes.get("content") or "").strip()

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag == "title":
            self.in_title = False
        elif normalized_tag == "head":
            self.complete = True

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)

    def snapshot(self, *, bytes_observed: int) -> ItemHeadSnapshot:
        return ItemHeadSnapshot(
            canonical_url=self.canonical_url,
            title="".join(self.title_parts).strip(),
            description=self.description,
            complete=self.complete,
            bytes_observed=bytes_observed,
        )


def inspect_item_head(html: str, *, max_bytes: int) -> ItemHeadSnapshot:
    parser = ItemHeadParser()
    encoded_prefix = html.encode("utf-8")[:max_bytes]
    prefix = encoded_prefix.decode("utf-8", errors="ignore")
    parser.feed(prefix)
    return parser.snapshot(bytes_observed=len(encoded_prefix))


class EarlyFilterBodyCollector:
    def __init__(
        self,
        *,
        terms: tuple[str, ...],
        max_bytes: int,
        catalog_title: str,
        canonical_validator: Callable[[str | None], bool],
    ) -> None:
        self.terms = terms
        self.max_bytes = max_bytes
        self.catalog_title = catalog_title
        self.canonical_validator = canonical_validator
        self.body = bytearray()
        self.parser = ItemHeadParser()
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.inspection_finished = False
        self.matched_terms: list[str] = []
        self.snapshot: ItemHeadSnapshot | None = None

    def __call__(self, chunk: bytes) -> int:
        self.body.extend(chunk)
        if not self.inspection_finished:
            remaining = max(self.max_bytes - (len(self.body) - len(chunk)), 0)
            inspected_chunk = chunk[:remaining]
            if inspected_chunk:
                self.parser.feed(self.decoder.decode(inspected_chunk, final=False))
            if self.parser.complete or len(self.body) >= self.max_bytes:
                self.inspection_finished = True
                self.snapshot = self.parser.snapshot(bytes_observed=min(len(self.body), self.max_bytes))
                if self.snapshot.complete and self.canonical_validator(self.snapshot.canonical_url):
                    description = self.snapshot.isolated_description(self.catalog_title)
                    self.matched_terms = matched_exclusion_terms(description or "", self.terms)
                    if self.matched_terms:
                        return CURL_WRITEFUNC_ERROR
        return len(chunk)

    @property
    def early_discarded(self) -> bool:
        return bool(self.matched_terms)

    def decoded_body(self) -> str:
        return bytes(self.body).decode("utf-8", errors="replace")
