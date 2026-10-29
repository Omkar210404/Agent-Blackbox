from blackbox.redact import redact_text
from blackbox.selftest import run_selftest


def test_selftest_passes():
    assert run_selftest()


def test_redaction_keeps_dates_ids_and_email_domain():
    out = redact_text("on 2026-10-12 10:30 id 3f2b8c1e-1a2b-4c3d-9e8f-0123456789ab "
                      "mail a.b@shop.example call +91 98765 43210 A/C 50100234567812")
    assert "2026-10-12 10:30" in out and "3f2b8c1e-1a2b" in out
    assert "[email]@shop.example" in out and "98765" not in out and "50100234567812" not in out
