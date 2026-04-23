from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any

import httpx

FATHOM_BASE_URL = "https://api.fathom.ai/external/v1"


class FathomClient:
    async def create_webhook(
        self,
        api_key: str,
        destination_url: str,
        triggered_for: list[str] | None = None,
        include_summary: bool = True,
        include_transcript: bool = True,
        include_action_items: bool = True,
    ) -> dict[str, Any]:
        body = {
            "destination_url": destination_url,
            "triggered_for": triggered_for or ["my_recordings"],
            "include_summary": include_summary,
            "include_transcript": include_transcript,
            "include_action_items": include_action_items,
        }
        headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(f"{FATHOM_BASE_URL}/webhooks", headers=headers, json=body)
            response.raise_for_status()
            return response.json()

    async def delete_webhook(self, api_key: str, webhook_id: str) -> None:
        headers = {"X-Api-Key": api_key}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.delete(f"{FATHOM_BASE_URL}/webhooks/{webhook_id}", headers=headers)
            if response.status_code not in (200, 204, 404):
                response.raise_for_status()


def _decode_webhook_secret(secret: str) -> bytes:
    encoded = secret[6:] if secret.startswith("whsec_") else secret
    padding = "=" * ((4 - (len(encoded) % 4)) % 4)
    return base64.urlsafe_b64decode((encoded + padding).encode("utf-8"))


def _extract_signatures(signature_header: str) -> list[str]:
    signatures: list[str] = []
    for token in signature_header.split():
        if "," in token:
            _, sig = token.split(",", 1)
            signatures.append(sig.strip())
        else:
            signatures.append(token.strip())
    return [s for s in signatures if s]


def verify_signature(
    raw_body: bytes,
    headers: dict[str, str] | Any,
    webhook_secret: str,
    tolerance_seconds: int = 300,
) -> bool:
    webhook_id = headers.get("webhook-id")
    webhook_timestamp = headers.get("webhook-timestamp")
    webhook_signature = headers.get("webhook-signature")

    if not webhook_id or not webhook_timestamp or not webhook_signature or not webhook_secret:
        return False

    try:
        ts = int(webhook_timestamp)
    except ValueError:
        return False

    if abs(int(time.time()) - ts) > tolerance_seconds:
        return False

    secret = _decode_webhook_secret(webhook_secret)
    signed = webhook_id.encode("utf-8") + b"." + webhook_timestamp.encode("utf-8") + b"." + raw_body
    digest = hmac.new(secret, signed, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    provided = _extract_signatures(webhook_signature)

    return any(hmac.compare_digest(expected, sig) for sig in provided)

