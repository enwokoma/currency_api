"""Outgoing email: SMTP and console backends, address checks, throttling."""

from __future__ import annotations

from fastapi.testclient import TestClient

import helpers
import mailer

# Captured before the autouse ``outbox`` fixture swaps delivery out.
REAL_DELIVER = mailer._deliver


class FakeSMTP:
    instances: list = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.calls = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append(("quit",))
        return False

    def starttls(self, context=None):
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message):
        if FakeSMTP.fail_next:
            raise OSError("connection reset")
        self.calls.append(("send", message["To"], message["Subject"], message.get_content()))


def _smtp(monkeypatch, security="starttls", port=587, username="mailer", fail=False):
    FakeSMTP.instances = []
    FakeSMTP.fail_next = fail
    monkeypatch.setattr(mailer, "_deliver", REAL_DELIVER)
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)
    for name, value in {
        "MAIL_BACKEND": "smtp",
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": port,
        "SMTP_SECURITY": security,
        "SMTP_USERNAME": username,
        "SMTP_PASSWORD": "pw",
        "SMTP_FROM": "API <no-reply@example.com>",
    }.items():
        monkeypatch.setattr(mailer, name, value)


def test_smtp_delivery_with_starttls_and_login(monkeypatch):
    _smtp(monkeypatch)
    assert mailer.send_email("ada@example.com", "Hello", "Body text") is True
    (server,) = FakeSMTP.instances
    assert (server.host, server.port, server.context) == ("smtp.example.com", 587, None)
    assert server.calls == [
        ("starttls",),
        ("login", "mailer", "pw"),
        ("send", "ada@example.com", "Hello", "Body text\n"),
        ("quit",),
    ]


def test_smtp_delivery_over_implicit_tls_without_login(monkeypatch):
    _smtp(monkeypatch, security="ssl", port=465, username="")
    assert mailer.send_email("ada@example.com", "Hi", "x") is True
    (server,) = FakeSMTP.instances
    assert server.port == 465 and server.context is not None
    assert [c[0] for c in server.calls] == ["send", "quit"]


def test_smtp_failure_is_reported_not_raised(monkeypatch, capsys):
    _smtp(monkeypatch, fail=True)
    assert mailer.send_email("ada@example.com", "Hi", "secret-body") is False
    out = capsys.readouterr().out
    assert "Failed to send 'Hi' to a***@example.com" in out and "secret-body" not in out


def test_unconfigured_smtp_drops_mail_and_logs_it(monkeypatch, capsys):
    _smtp(monkeypatch)
    monkeypatch.setattr(mailer, "SMTP_HOST", "")
    assert mailer.is_configured() is False
    assert mailer.send_email("ada@example.com", "Hi", "secret-body") is False
    out = capsys.readouterr().out
    assert "SMTP is not configured" in out and "secret-body" not in out
    assert FakeSMTP.instances == []


def test_console_backend_prints_the_message(monkeypatch, capsys):
    monkeypatch.setattr(mailer, "_deliver", REAL_DELIVER)
    monkeypatch.setattr(mailer, "MAIL_BACKEND", "console")
    assert mailer.is_configured() is True
    assert mailer.send_email("ada@example.com", "Code", "Your code is 123456") is True
    out = capsys.readouterr().out
    assert "To: ada@example.com" in out and "Your code is 123456" in out


def test_invalid_recipients_are_refused(outbox):
    for bad in ("", "no-at-sign", "a@b", "a b@example.com", "a@example.com\r\nBcc: x@example.com", "a@@example.com", "x" * 250 + "@example.com"):
        assert mailer.send_email(bad, "Hi", "x") is False, bad
    assert outbox == []
    assert mailer.send_email("first.last+tag@sub.example.co", "Hi", "x") is True


def test_mail_to_one_recipient_is_throttled(monkeypatch, outbox):
    monkeypatch.setattr(mailer, "MAIL_PER_RECIPIENT_LIMITS", (2, 10))
    sent = [mailer.send_email("flood@example.com", f"#{i}", "x") for i in range(3)]
    assert sent == [True, True, False]
    assert mailer.send_email("FLOOD@example.com", "case", "x") is False, "the limit ignores address case"
    assert mailer.send_email("other@example.com", "Hi", "x") is True
    assert [m["Subject"] for m in outbox] == ["#0", "#1", "Hi"]


def test_startup_warns_when_mail_cannot_be_delivered(monkeypatch, capsys):
    monkeypatch.setattr(mailer, "SMTP_HOST", "")
    with TestClient(helpers.main.app):
        pass
    assert "email is not configured" in capsys.readouterr().out

    monkeypatch.setattr(helpers.main, "MAIL_BACKEND", "console")
    with TestClient(helpers.main.app):
        pass
    assert "MAIL_BACKEND=console prints every email" in capsys.readouterr().out
