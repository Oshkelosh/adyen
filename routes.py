"""Adyen addon routes — thin delegates to shared payment route factory."""

from __future__ import annotations

from typing import Any

from app.addons.payments.shared_routes import build_payment_routers


def _parse_adyen_config_form(form: Any) -> tuple[dict[str, Any], bool]:
    return (
        {
            "api_key": form.get("api_key", ""),
            "merchant_account": form.get("merchant_account", ""),
            "client_key": form.get("client_key", ""),
            "webhook_hmac_key": form.get("webhook_hmac_key", ""),
            "environment": form.get("environment", "test"),
            "return_url": form.get("return_url", ""),
            "cancel_url": form.get("cancel_url", ""),
        },
        form.get("is_enabled") == "on",
    )


admin_router, api_router, jinja_env = build_payment_routers(
    "adyen",
    template_name="adyen_config.html",
    page_title="Adyen Settings",
    secret_keys=("api_key", "webhook_hmac_key"),
    signature_header="hmacsignature",
    parse_config_form=_parse_adyen_config_form,
)
