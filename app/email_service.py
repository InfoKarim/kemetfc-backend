import smtplib
from email.message import EmailMessage

from app.config import (
    get_smtp_from_email,
    get_smtp_host,
    get_smtp_password,
    get_smtp_port,
    get_smtp_username,
)


class EmailSendError(Exception):
    pass


def send_password_reset_email(to_email: str, code: str) -> None:
    username = get_smtp_username()
    password = get_smtp_password()

    if not username or not password:
        raise EmailSendError(
            "SMTP_USERNAME and SMTP_PASSWORD are not configured"
        )

    message = EmailMessage()
    message["Subject"] = "Your Kemet FC password reset code"
    message["From"] = get_smtp_from_email()
    message["To"] = to_email
    message.set_content(
        f"Your Kemet FC password reset code is: {code}\n\n"
        "This code expires in 15 minutes. If you didn't request a "
        "password reset, you can ignore this email."
    )

    try:
        with smtplib.SMTP(get_smtp_host(), get_smtp_port(), timeout=10) as smtp:
            smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as error:
        raise EmailSendError(f"Could not send email: {error}") from error
