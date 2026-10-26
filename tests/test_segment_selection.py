import pytest

from bvc_renta_fija.client import (
    BVCRentaFija,
    BVCSegmentSelectionError,
    SEGMENTO_LABELS,
)


class FakeLocator:
    def __init__(self, count=1, click_error=None):
        self._count = count
        self.click_error = click_error
        self.clicked = False

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def click(self, timeout=None):
        if self.click_error:
            raise self.click_error
        self.clicked = True


class FakePage:
    def __init__(self, locator):
        self.locator = locator
        self.requested_label = None
        self.exact = None
        self.waited = []

    def get_by_text(self, label, exact=False):
        self.requested_label = label
        self.exact = exact
        return self.locator

    def wait_for_timeout(self, milliseconds):
        self.waited.append(milliseconds)


def test_public_debt_segment_labels_have_correct_accents():
    assert SEGMENTO_LABELS["deuda_publica_segmento_publico"] == (
        "Deuda pública segmento público"
    )
    assert SEGMENTO_LABELS["deuda_publica_segmento_privado"] == (
        "Deuda pública segmento privado"
    )
    assert SEGMENTO_LABELS["deuda_corporativa"] == "Deuda corporativa"
    assert not any("Ã" in label or "Â" in label for label in SEGMENTO_LABELS.values())


def test_segment_selection_uses_exact_label_and_clicks_requested_tab():
    locator = FakeLocator()
    page = FakePage(locator)

    BVCRentaFija._seleccionar_segmento(
        page, "deuda_publica_segmento_publico"
    )

    assert page.requested_label == "Deuda pública segmento público"
    assert page.exact is True
    assert locator.clicked is True
    assert page.waited == [1500]


def test_missing_segment_tab_fails_closed():
    locator = FakeLocator(count=0)
    page = FakePage(locator)

    with pytest.raises(BVCSegmentSelectionError, match="se cancela la extracción"):
        BVCRentaFija._seleccionar_segmento(
            page, "deuda_publica_segmento_privado"
        )

    assert page.requested_label == "Deuda pública segmento privado"
    assert page.exact is True
    assert page.waited == []


def test_click_failure_fails_closed():
    locator = FakeLocator(click_error=RuntimeError("click failed"))
    page = FakePage(locator)

    with pytest.raises(BVCSegmentSelectionError, match="evitar mezclar segmentos"):
        BVCRentaFija._seleccionar_segmento(page, "deuda_corporativa")

    assert page.waited == []
