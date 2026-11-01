"""Install macOS launchd job for a 07:00 local-time daily digest."""
import os
import plistlib
import subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1]
text=(root/'.env').read_text()
values=dict(line.split('=',1) for line in text.splitlines() if '=' in line and not line.lstrip().startswith('#'))
if not all(values.get(k) for k in ('GEMINI_API_KEY','SMTP_HOST','SMTP_TO','SMTP_PASSWORD')):
    raise SystemExit('Configure Gemini and Gmail credentials before installing email schedule.')
label='com.personal.antifilterbubble.daily'
path=Path.home()/'Library/LaunchAgents'/f'{label}.plist'
path.parent.mkdir(parents=True,exist_ok=True)
job={'Label':label,'ProgramArguments':['/bin/sh',str(root/'scripts/daily.sh')],
     'WorkingDirectory':str(root),'StartCalendarInterval':{'Hour':7,'Minute':0},
     'EnvironmentVariables':{'BRIEFING_SEND_EMAIL':'true'},
     'StandardOutPath':str(root/'logs/launchd.stdout.log'),
     'StandardErrorPath':str(root/'logs/launchd.stderr.log')}
with path.open('wb') as f:plistlib.dump(job,f)
subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}',str(path)],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(path)],check=True)
print('Installed:',path)
print('Runs at 07:00 local computer time while this Mac is available.')
