"""The committed RDS CA bundle is the file SECURITY.md describes, and loads as a trust store."""

import hashlib
import ssl
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "docker" / "certs" / "rds-global-bundle.pem"
# Refreshing the bundle means changing this, SECURITY.md and the Dockerfile's comment together.
BUNDLE_SHA256 = "90a6f1a7978a1b1b096dca6c57511ca48d3b8cf052f8e704baf358f34ea2f21b"


def test_bundle_matches_the_recorded_hash() -> None:
    assert hashlib.sha256(BUNDLE.read_bytes()).hexdigest() == BUNDLE_SHA256
    assert BUNDLE_SHA256 in (ROOT / "SECURITY.md").read_text(encoding="utf-8")


def test_bundle_holds_only_ca_certificates() -> None:
    text = BUNDLE.read_text(encoding="ascii")
    count = text.count("-----BEGIN CERTIFICATE-----")
    assert count == text.count("-----END CERTIFICATE-----") > 0
    context = ssl.create_default_context(cafile=str(BUNDLE))
    assert context.cert_store_stats()["x509_ca"] == count
