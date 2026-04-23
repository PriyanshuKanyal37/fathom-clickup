import base64
import hashlib
import hmac
import time

from app.fathom import verify_signature


def _make_signature(secret: str, webhook_id: str, timestamp: int, body: bytes) -> str:
    encoded = secret[6:] if secret.startswith("whsec_") else secret
    padding = "=" * ((4 - (len(encoded) % 4)) % 4)
    key = base64.urlsafe_b64decode((encoded + padding).encode("utf-8"))
    signed = webhook_id.encode() + b"." + str(timestamp).encode() + b"." + body
    digest = hmac.new(key, signed, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def test_verify_signature_valid():
    secret = "whsec_dGVzdF9zZWNyZXRfMTIzNDU2Nzg5MA"
    body = b'{"recording_id":123}'
    webhook_id = "msg_abc123"
    ts = int(time.time())
    sig = _make_signature(secret, webhook_id, ts, body)
    headers = {
        "webhook-id": webhook_id,
        "webhook-timestamp": str(ts),
        "webhook-signature": f"v1,{sig}",
    }
    assert verify_signature(body, headers, secret, tolerance_seconds=300)


def test_verify_signature_invalid():
    secret = "whsec_dGVzdF9zZWNyZXRfMTIzNDU2Nzg5MA"
    body = b'{"recording_id":123}'
    headers = {
        "webhook-id": "msg_abc123",
        "webhook-timestamp": str(int(time.time())),
        "webhook-signature": "v1,invalid",
    }
    assert not verify_signature(body, headers, secret, tolerance_seconds=300)

