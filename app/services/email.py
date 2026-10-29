"""Pluggable email delivery; never log verification or reset codes."""

class EmailSender:
    def send(self, recipient: str, subject: str, code: str) -> None:
        raise NotImplementedError


class ConsoleEmailSender(EmailSender):
    def send(self, recipient: str, subject: str, code: str) -> None:
        import logging
        logging.getLogger(__name__).info('Email queued for %s (%s)', recipient, subject)


sender: EmailSender = ConsoleEmailSender()
