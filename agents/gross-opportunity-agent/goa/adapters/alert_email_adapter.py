"""
Alert email adapter — for free portals that forward keyword alerts.
Reads a shared mailbox by IMAP (or mailbox API), parses each alert email
into a raw record, and yields it.
This is how portals that prohibit automated scraping are handled.
Keywords are set at the portal by Meri, per BAS-5.
Gap: mailbox credentials and IMAP host per source.
"""

import email
import imaplib
import logging
from typing import Iterator

from .base import Adapter

log = logging.getLogger(__name__)


def _load_secret(secret_ref: str) -> str:
    from google.cloud import secretmanager  # type: ignore
    client = secretmanager.SecretManagerServiceClient()
    return client.access_secret_version(name=secret_ref).payload.data.decode()


class AlertEmailAdapter(Adapter):
    def pull(self, mode: str, watermark: str | None) -> Iterator[dict]:
        cfg = self.cfg.get("config", {})
        imap_host = cfg.get("imap_host")
        imap_user = cfg.get("imap_user")
        password_ref = cfg.get("password_secret_ref")
        if not imap_host or not imap_user or not password_ref:
            log.error("AlertEmailAdapter %s: missing imap_host, imap_user, or password_secret_ref", self.source_id)
            return

        password = _load_secret(password_ref)
        mailbox = imaplib.IMAP4_SSL(imap_host)
        mailbox.login(imap_user, password)
        mailbox.select("INBOX")

        criteria = "UNSEEN" if mode == "delta" else "ALL"
        _, uids = mailbox.search(None, criteria)
        for uid in uids[0].split():
            _, msg_data = mailbox.fetch(uid, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            yield {
                "source_id": self.source_id,
                "uid": uid.decode(),
                "subject": msg.get("Subject", ""),
                "from": msg.get("From", ""),
                "date": msg.get("Date", ""),
                "body": self._body(msg),
                "_record_type": "itb",  # invitation emails are always ITB context
            }
            self._rate_limit_sleep()

        mailbox.logout()

    @staticmethod
    def _body(msg: email.message.Message) -> str:
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(errors="replace")
        return ""
