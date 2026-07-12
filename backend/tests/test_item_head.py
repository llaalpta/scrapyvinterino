from curl_cffi.curl import CURL_WRITEFUNC_ERROR

from vinted_monitor.providers.item_head import EarlyFilterBodyCollector, inspect_item_head


def test_item_head_parser_handles_split_utf8_and_matches_only_after_complete_head() -> None:
    html = (
        '<html><head><link rel="canonical" href="https://www.vinted.es/items/123-camiseta">'
        '<title>Camiseta azul</title><meta name="description" content="Edicion nino">'
        "</head><body>resto</body></html>"
    ).replace("nino", "niño")
    collector = EarlyFilterBodyCollector(
        terms=("NINO",),
        max_bytes=4096,
        canonical_validator=lambda value: value == "https://www.vinted.es/items/123-camiseta",
    )
    encoded = html.encode("utf-8")
    results = [collector(encoded[offset : offset + 7]) for offset in range(0, len(encoded), 7)]

    assert CURL_WRITEFUNC_ERROR in results
    assert collector.early_discarded is True
    assert collector.matched_terms == ["NINO"]


def test_item_head_collector_does_not_abort_wrong_canonical() -> None:
    html = (
        '<html><head><link rel="canonical" href="https://www.vinted.es/items/999-other">'
        '<title>Prohibido</title><meta name="description" content="Prohibido"></head></html>'
    )
    collector = EarlyFilterBodyCollector(
        terms=("prohibido",),
        max_bytes=4096,
        canonical_validator=lambda _value: False,
    )

    assert collector(html.encode()) == len(html.encode())
    assert collector.early_discarded is False


def test_item_head_shadow_reports_incomplete_prefix_without_decision() -> None:
    snapshot = inspect_item_head("<html><head><title>Parcial", max_bytes=64)

    assert snapshot.complete is False
    assert snapshot.title == "Parcial"
