"""Run locally: password input is hidden and never printed."""
import getpass
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'.env'
password=getpass.getpass('Gmail app password (hidden): ').replace(' ','')
if len(password)!=16 or not password.isalpha():
    raise SystemExit('Expected the 16-letter Google app password. Configuration unchanged.')
values={'SMTP_HOST':'smtp.gmail.com','SMTP_PORT':'465','SMTP_SSL':'true','SMTP_STARTTLS':'false','SMTP_USERNAME':'jiaohu368@gmail.com','SMTP_FROM':'jiaohu368@gmail.com','SMTP_TO':'jiaohu368@gmail.com','SMTP_PASSWORD':password,'EMAIL_PREVIEW_ONLY':'true'}
existing=p.read_text() if p.exists() else ''
lines=[s for s in existing.splitlines() if s.split('=',1)[0] not in values]
p.write_text('\n'.join(lines+[k+'='+v for k,v in values.items()])+'\n');p.chmod(0o600)
print('Gmail configuration saved. Preview remains default; use send command for delivery.')
