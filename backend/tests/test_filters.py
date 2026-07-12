from vinted_monitor.db.models import Item
from vinted_monitor.services.filters import evaluate_exclusion_filters


def filter_snapshot(*terms: str) -> list[dict]:
    return [{"name": "Terminos excluyentes", "definition": {"blacklist_terms": list(terms)}}]


def test_exclusion_filters_match_description_case_and_accents() -> None:
    item = Item(title="Permitido", url="https://www.vinted.es/items/1", description="Edici\u00f3n permitida")

    decision = evaluate_exclusion_filters(item, filter_snapshot("EDICION"))

    assert decision.status == "discarded"
    assert decision.matched_terms == ["EDICION"]


def test_exclusion_filters_ignore_every_non_description_field() -> None:
    item = Item(
        title="prohibido",
        brand="prohibido",
        size="prohibido",
        status="prohibido",
        seller_login="prohibido",
        seller_country="prohibido",
        description="Descripcion permitida",
        color="prohibido",
        category="prohibido",
        seller_badges=["prohibido"],
        url="https://www.vinted.es/items/1",
    )

    decision = evaluate_exclusion_filters(item, filter_snapshot("prohibido"))

    assert decision.status == "passed"
    assert decision.matched_terms == []


def test_exclusion_filters_accept_observed_empty_description() -> None:
    item = Item(title="Permitido", url="https://www.vinted.es/items/1", description="")

    decision = evaluate_exclusion_filters(item, filter_snapshot("prohibido"))

    assert decision.status == "passed"
