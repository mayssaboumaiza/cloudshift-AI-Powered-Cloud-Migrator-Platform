"""email_service.py — Send transactional emails via Gmail SMTP (aiosmtplib)."""
import os
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

logger = logging.getLogger(__name__)

_SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
_SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
_SMTP_USER = os.environ.get("SMTP_USER", "")
_SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
_SMTP_FROM = os.environ.get("SMTP_FROM", f"CloudShift <{_SMTP_USER}>")
_FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")


def _invitation_html(invite_link: str, invited_by: str, role_label: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f0f4ff;font-family:system-ui,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="padding:40px 0;">
    <tr><td align="center">
      <table width="520" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(99,102,241,.12);">
        <!-- Header -->
        <tr>
          <td style="background:linear-gradient(135deg,#4f46e5,#0284c7);padding:32px 40px;text-align:center;">
            <div style="font-size:26px;font-weight:900;color:#fff;letter-spacing:-0.04em;">☁ CloudShift</div>
            <div style="font-size:12px;color:rgba(255,255,255,.7);margin-top:4px;letter-spacing:.1em;text-transform:uppercase;">Cloud Migration Platform</div>
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="padding:36px 40px;">
            <p style="margin:0 0 12px;font-size:20px;font-weight:700;color:#1e3a5f;">Vous avez été invité !</p>
            <p style="margin:0 0 20px;font-size:14px;color:#475569;line-height:1.6;">
              <strong>{invited_by}</strong> vous invite à rejoindre CloudShift en tant que <strong>{role_label}</strong>.
              Ce lien est valable <strong>72 heures</strong>.
            </p>
            <table cellpadding="0" cellspacing="0" style="margin:28px 0;">
              <tr>
                <td style="background:linear-gradient(135deg,#4f46e5,#0284c7);border-radius:10px;">
                  <a href="{invite_link}" style="display:inline-block;padding:14px 32px;color:#fff;font-size:14px;font-weight:700;text-decoration:none;letter-spacing:.02em;">
                    Accepter l'invitation →
                  </a>
                </td>
              </tr>
            </table>
            <p style="margin:0;font-size:12px;color:#94a3b8;">
              Ou copiez ce lien dans votre navigateur :<br>
              <span style="color:#6366f1;word-break:break-all;">{invite_link}</span>
            </p>
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="padding:16px 40px;border-top:1px solid #e2e8f0;text-align:center;">
            <p style="margin:0;font-size:11px;color:#94a3b8;">
              Si vous ne vous attendiez pas à cette invitation, ignorez simplement cet email.<br>
              CloudShift v4.0 · Talan Tunisie
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


async def send_invitation_email(
    to_email: str,
    token: str,
    invited_by_name: str,
    role: str,
) -> None:
    """Send an invitation email. Raises on SMTP failure."""
    if not _SMTP_USER or not _SMTP_PASSWORD:
        logger.warning("SMTP not configured — skipping invitation email to %s", to_email)
        return

    role_label = "Administrateur" if role == "admin" else "Cloud Engineer"
    invite_link = f"{_FRONTEND_URL}/invite/{token}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Invitation à rejoindre CloudShift — {role_label}"
    msg["From"] = _SMTP_FROM
    msg["To"] = to_email

    text_body = (
        f"Vous avez été invité par {invited_by_name} à rejoindre CloudShift ({role_label}).\n\n"
        f"Acceptez votre invitation ici (valable 72h) :\n{invite_link}\n\n"
        "Si vous n'attendiez pas cette invitation, ignorez cet email."
    )
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(_invitation_html(invite_link, invited_by_name, role_label), "html"))

    await aiosmtplib.send(
        msg,
        hostname=_SMTP_HOST,
        port=_SMTP_PORT,
        username=_SMTP_USER,
        password=_SMTP_PASSWORD,
        start_tls=True,
    )
    logger.info("Invitation email sent to %s", to_email)
