"""
Adyen payment integration.

Collects payments via Adyen Checkout and handles webhooks.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from typing import Any, Dict, List, Literal, Mapping

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field, SecretStr

from app.addons.payments.base import PaymentAddon
from app.addons.payments.helpers import create_payment_error, effective_redirect_url, extract_order_id, mock_checkout
from schemas.payment import PaymentWebhookOutcome
from app.addons.log import info, warning
from app.addons.config_serialization import dump_addon_config


def _adyen_hmac_payload(item: dict[str, Any]) -> str:
    """Build Adyen's HMAC signing string from a NotificationRequestItem."""
    amount = item.get("amount", {}) or {}
    fields = [
        str(item.get("pspReference", "")),
        str(item.get("originalReference", "")),
        str(item.get("merchantAccountCode", "")),
        str(item.get("merchantReference", "")),
        str(amount.get("value", "")),
        str(amount.get("currency", "")),
        str(item.get("eventCode", "")),
        str(item.get("success", "")),
    ]
    escaped = [f.replace("\\", "\\\\").replace(":", "\\:") for f in fields]
    return ":".join(escaped)

AdyenEnvironment = Literal["test", "live"]

_CHECKOUT_BASES: dict[AdyenEnvironment, str] = {
    "test": "https://checkout-test.adyen.com/v71",
    "live": "https://checkout-live.adyen.com/v71",
}


class AdyenConfig(BaseModel):
    api_key: SecretStr = Field(default=..., description="Adyen API key")
    merchant_account: str = Field(default=..., description="Adyen merchant account")
    client_key: str = Field(default="", description="Adyen client key (Drop-in / Components)")
    webhook_hmac_key: SecretStr = Field(
        default=...,
        description="HMAC key for webhook signature verification",
    )
    environment: AdyenEnvironment = Field(default="test")
    return_url: str = Field(
        default="",
        description="Optional override for return redirect (leave blank to use Site URL)",
    )
    cancel_url: str = Field(
        default="",
        description="Optional override for cancel redirect (leave blank to use Site URL)",
    )

    @classmethod
    def config_model(cls):
        return cls


class AdyenAddon(PaymentAddon):
    addon_id: str = "adyen"
    addon_name: str = "Adyen"
    addon_description: str = "Accept payments via Adyen Checkout."
    addon_category: str = "payment"
    version: str = "1.0.0"
    is_enabled: bool = False

    _config: Dict[str, Any] | None = None
    _api_key: str | None = None
    _merchant_account: str | None = None
    _client_key: str = ""
    _webhook_hmac_key: str | None = None
    _environment: AdyenEnvironment = "test"
    _return_url: str = ""
    _cancel_url: str = ""
    _checkout_base: str = _CHECKOUT_BASES["test"]

    @classmethod
    def config_schema(cls):
        return AdyenConfig

    async def initialize(self, config: dict) -> None:
        validated = self.config_schema()(**config)
        self._config = dump_addon_config(validated)
        self._api_key = validated.api_key.get_secret_value()
        self._merchant_account = validated.merchant_account
        self._client_key = validated.client_key
        self._webhook_hmac_key = validated.webhook_hmac_key.get_secret_value()
        self._environment = validated.environment
        self._return_url = validated.return_url
        self._cancel_url = validated.cancel_url
        self._checkout_base = _CHECKOUT_BASES[self._environment]
        self.is_enabled = True
        info("Adyen", "Initialized (environment={})", self._environment)

    async def validate_config(self, config: dict) -> None:
        from app.core.exceptions import ValidationError

        validated = self.config_schema()(**config)
        api_key = validated.api_key.get_secret_value()
        if not api_key:
            return
        checkout_base = _CHECKOUT_BASES[validated.environment]
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{checkout_base}/paymentMethods",
                headers={"x-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "merchantAccount": validated.merchant_account,
                    "countryCode": "US",
                    "amount": {"currency": "USD", "value": 100},
                },
            )
        if resp.status_code == 401:
            raise ValidationError(message="Invalid API key — check your credentials")
        if resp.status_code == 403:
            raise ValidationError(
                message="API key is valid but missing required permissions: checkout:read"
            )
        if resp.status_code >= 400:
            raise ValidationError(message="Adyen rejected the API key")

    async def shutdown(self) -> None:
        self._api_key = None
        self._merchant_account = None
        self._webhook_hmac_key = None
        self.is_enabled = False

    def _auth_headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key or "",
            "Content-Type": "application/json",
        }

    async def create_payment(
        self,
        amount: int,
        currency: str,
        order_id: str,
        customer_email: str,
        *,
        return_url: str | None = None,
        cancel_url: str | None = None,
    ) -> Dict[str, Any]:
        if not self._api_key or not self._merchant_account:
            return mock_checkout("adyen", order_id, amount, currency)

        body: dict[str, Any] = {
            "merchantAccount": self._merchant_account,
            "amount": {"currency": currency.upper(), "value": amount},
            "reference": order_id,
            "returnUrl": effective_redirect_url(
                self._return_url, fallback=return_url or ""
            ),
            "metadata": {"order_id": order_id},
        }
        if customer_email:
            body["shopperEmail"] = customer_email

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    f"{self._checkout_base}/sessions",
                    headers=self._auth_headers(),
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()
                session_id = data.get("id", "")
                return {
                    "success": True,
                    "payment_id": session_id,
                    "session_id": session_id,
                    "url": data.get("url", ""),
                    "order_id": order_id,
                }
        except Exception as exc:
            warning("Adyen", "create_payment error: {}", exc)
            return create_payment_error("adyen", exc, order_id)

    async def confirm_payment(self, payment_id: str) -> Dict[str, Any]:
        status = await self.get_payment_status(payment_id)
        if status.get("status") == "error":
            return {"success": False, "error": status.get("detail", "Unknown error")}
        return {
            "success": True,
            "payment_id": payment_id,
            "status": status.get("status", "unknown"),
            "amount": status.get("amount", 0),
        }

    async def refund_payment(self, payment_id: str, amount: int) -> Dict[str, Any]:
        if not self._api_key or not self._merchant_account:
            return {"success": False, "error": "Adyen credentials not configured"}

        body = {
            "merchantAccount": self._merchant_account,
            "amount": {"value": amount},
            "reference": f"refund_{payment_id}",
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    f"{self._checkout_base}/payments/{payment_id}/refunds",
                    headers=self._auth_headers(),
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "success": True,
                    "refund_id": data.get("pspReference", data.get("id", "")),
                    "amount": amount,
                    "status": data.get("status", "received"),
                }
        except Exception as exc:
            warning("Adyen", "refund_payment({}) error: {}", payment_id, exc)
            return {"success": False, "error": str(exc)}

    async def get_payment_status(self, payment_id: str) -> Dict[str, Any]:
        if not self._api_key:
            return {"payment_id": payment_id, "status": "error", "detail": "Not configured"}

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    f"{self._checkout_base}/sessions/{payment_id}",
                    headers=self._auth_headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "payment_id": payment_id,
                    "status": data.get("status", "unknown"),
                    "amount": data.get("amount", {}).get("value", 0),
                    "currency": data.get("amount", {}).get("currency", "usd"),
                }
        except Exception as exc:
            warning("Adyen", "get_payment_status({}) error: {}", payment_id, exc)
            return {"payment_id": payment_id, "status": "error", "detail": str(exc)}

    def webhook_signature_header(self) -> str:
        return "hmacsignature"

    async def verify_webhook(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes,
    ) -> bool:
        """Verify the Adyen HMAC signature carried in the notification item."""
        del headers
        if not self._webhook_hmac_key:
            warning("Adyen", "verify_webhook skipped: HMAC key not configured")
            return False
        try:
            key = binascii.unhexlify(self._webhook_hmac_key)
        except (binascii.Error, ValueError):
            warning("Adyen", "verify_webhook: HMAC key is not valid hex")
            return False
        try:
            payload = json.loads(body.decode("utf-8"))
            items = payload.get("notificationItems", [])
            if not items:
                return False
            verified_any = False
            for entry in items:
                item = entry.get("NotificationRequestItem", {}) or {}
                provided = str(
                    (item.get("additionalData") or {}).get("hmacSignature", "")
                )
                if not provided:
                    return False
                signing_string = _adyen_hmac_payload(item).encode("utf-8")
                expected = base64.b64encode(
                    hmac.new(key, signing_string, hashlib.sha256).digest()
                ).decode("utf-8")
                if not hmac.compare_digest(expected, provided):
                    return False
                verified_any = True
            return verified_any
        except Exception as exc:
            warning("Adyen", "verify_webhook error: {}", exc)
            return False

    async def parse_webhook(
        self, payload: Dict[str, Any], signature: str
    ) -> PaymentWebhookOutcome:
        try:
            notification_items = payload.get("notificationItems", [])
            if not notification_items:
                event_type = payload.get("type", payload.get("eventCode", ""))
                event_data = payload.get("data", payload)
                event_id = str(payload.get("id", payload.get("pspReference", "")))
            else:
                item = notification_items[0].get("NotificationRequestItem", {})
                event_type = item.get("eventCode", "")
                event_data = item
                event_id = str(item.get("pspReference", item.get("originalReference", "")))

            info("Adyen", "Webhook received: {}", event_type)

            if event_type in ("AUTHORISATION", "CAPTURE") and event_data.get("success") == "true":
                additional = event_data.get("additionalData", {})
                metadata = dict(event_data.get("metadata") or {})
                if additional.get("metadata.order_id"):
                    metadata["order_id"] = additional.get("metadata.order_id")
                order_id = extract_order_id(metadata)
                payment_id = event_data.get("pspReference") or event_data.get("originalReference")
                return PaymentWebhookOutcome(
                    handled=True,
                    event_id=event_id,
                    event_type=event_type,
                    mark_paid=order_id is not None,
                    order_id=order_id,
                    payment_id=str(payment_id) if payment_id else None,
                )

            return PaymentWebhookOutcome(
                handled=True,
                event_id=event_id,
                event_type=event_type,
            )
        except Exception as exc:
            warning("Adyen", "parse_webhook error: {}", exc)
            return PaymentWebhookOutcome(handled=False, error=str(exc))

    def get_routers(self) -> List[APIRouter]:
        from app.addons.payments.adyen.routes import api_router

        return [api_router]

    def get_admin_routes(self) -> List[APIRouter]:
        from app.addons.payments.adyen.routes import admin_router

        return [admin_router]

    def get_admin_templates(self) -> str:
        from pathlib import Path

        return str(Path(__file__).resolve().parent / "templates")

    def get_admin_static(self) -> str:
        from pathlib import Path

        return str(Path(__file__).resolve().parent / "static")
