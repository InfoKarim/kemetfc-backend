import smtplib

import pytest

from app import email_service


def test_send_password_reset_email_requires_credentials(monkeypatch):
    monkeypatch.setattr(email_service, "get_smtp_username", lambda: "")
    monkeypatch.setattr(email_service, "get_smtp_password", lambda: "secret")

    with pytest.raises(email_service.EmailSendError, match="not configured"):
        email_service.send_password_reset_email("parent@example.com", "123456")


def test_send_password_reset_email_sends_via_smtp(monkeypatch):
    monkeypatch.setattr(email_service, "get_smtp_username", lambda: "bot")
    monkeypatch.setattr(email_service, "get_smtp_password", lambda: "secret")
    monkeypatch.setattr(email_service, "get_smtp_from_email", lambda: "noreply@kemetfc.com")
    monkeypatch.setattr(email_service, "get_smtp_host", lambda: "smtp.example.com")
    monkeypatch.setattr(email_service, "get_smtp_port", lambda: 587)

    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"] = host
            sent["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def starttls(self):
            sent["starttls"] = True

        def login(self, username, password):
            sent["login"] = (username, password)

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)

    email_service.send_password_reset_email("parent@example.com", "654321")

    assert sent["host"] == "smtp.example.com"
    assert sent["port"] == 587
    assert sent["starttls"] is True
    assert sent["login"] == ("bot", "secret")
    assert sent["message"]["To"] == "parent@example.com"
    assert sent["message"]["From"] == "noreply@kemetfc.com"
    assert "654321" in sent["message"].get_content()


def test_send_password_reset_email_wraps_smtp_exception(monkeypatch):
    monkeypatch.setattr(email_service, "get_smtp_username", lambda: "bot")
    monkeypatch.setattr(email_service, "get_smtp_password", lambda: "secret")
    monkeypatch.setattr(email_service, "get_smtp_from_email", lambda: "noreply@kemetfc.com")
    monkeypatch.setattr(email_service, "get_smtp_host", lambda: "smtp.example.com")
    monkeypatch.setattr(email_service, "get_smtp_port", lambda: 587)

    class FailingSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            raise smtplib.SMTPConnectError(421, "cannot connect")

        def __exit__(self, *exc_info):
            return False

    monkeypatch.setattr(email_service.smtplib, "SMTP", FailingSMTP)

    with pytest.raises(email_service.EmailSendError, match="Could not send email"):
        email_service.send_password_reset_email("parent@example.com", "111111")


def test_send_password_reset_email_wraps_os_error(monkeypatch):
    monkeypatch.setattr(email_service, "get_smtp_username", lambda: "bot")
    monkeypatch.setattr(email_service, "get_smtp_password", lambda: "secret")
    monkeypatch.setattr(email_service, "get_smtp_from_email", lambda: "noreply@kemetfc.com")
    monkeypatch.setattr(email_service, "get_smtp_host", lambda: "smtp.example.com")
    monkeypatch.setattr(email_service, "get_smtp_port", lambda: 587)

    class FailingSMTP:
        def __init__(self, host, port, timeout=None):
            raise OSError("network unreachable")

    monkeypatch.setattr(email_service.smtplib, "SMTP", FailingSMTP)

    with pytest.raises(email_service.EmailSendError, match="Could not send email"):
        email_service.send_password_reset_email("parent@example.com", "222222")
