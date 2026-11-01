import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from briefing_pipeline.core import deliver_email

class EmailDeliveryTests(unittest.TestCase):
    def test_ssl_smtp_sends_only_when_explicit(self):
        env={'EMAIL_PREVIEW_ONLY':'false','SMTP_HOST':'smtp.gmail.com','SMTP_PORT':'465','SMTP_SSL':'true','SMTP_FROM':'jiaohu368@gmail.com','SMTP_TO':'jiaohu368@gmail.com','SMTP_USERNAME':'jiaohu368@gmail.com','SMTP_PASSWORD':'test-only'}
        with tempfile.TemporaryDirectory() as tmp, patch('briefing_pipeline.core.smtplib.SMTP_SSL') as smtp:
            server=smtp.return_value.__enter__.return_value
            server.send_message.return_value={}
            result=deliver_email('Digest',env,Path(tmp)/'preview.txt')
            self.assertEqual(result,'sent')
            server.login.assert_called_once_with('jiaohu368@gmail.com','test-only')
            self.assertEqual(server.send_message.call_count,1)
    def test_preview_never_opens_smtp(self):
        with tempfile.TemporaryDirectory() as tmp, patch('briefing_pipeline.core.smtplib.SMTP_SSL') as smtp:
            self.assertEqual(deliver_email('Digest',{'EMAIL_PREVIEW_ONLY':'true'},Path(tmp)/'email.txt'),'preview')
            smtp.assert_not_called()
