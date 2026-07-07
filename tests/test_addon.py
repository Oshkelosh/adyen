"""Minimal unit tests for the adyen addon."""

from app.addons.payments.adyen.addon import AdyenAddon


def test_addon_identity():
    assert AdyenAddon.addon_id == "adyen"
    assert AdyenAddon.addon_category == "payment"
