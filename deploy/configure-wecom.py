"""Merge only Enterprise WeChat settings into an existing server .env."""
import argparse
from datetime import datetime
from getpass import getpass
from pathlib import Path
from urllib.parse import urlparse
import os

keys=('WECOM_CORP_ID','WECOM_AGENT_ID','WECOM_SECRET','WECOM_PUBLIC_URL')
parser=argparse.ArgumentParser(description='Configure Enterprise WeChat without changing other deployment settings.')
parser.add_argument('--file',type=Path,help='Private configuration file. Never commit or publish this file.')
args=parser.parse_args()
root=Path(__file__).resolve().parent.parent
env=root/'.env'
if not env.is_file():raise SystemExit('Missing .env: configure the existing deployment first.')
if args.file:
    values={}
    for line in args.file.read_text(encoding='utf-8-sig').splitlines():
        key,separator,value=line.strip().partition('=')
        if separator and key in keys:values[key]=value.strip().strip('"\'')
else:
    values={'WECOM_CORP_ID':input('CorpID: ').strip(),'WECOM_AGENT_ID':input('AgentID: ').strip(),
        'WECOM_PUBLIC_URL':input('Website root URL including port: ').strip(),'WECOM_SECRET':getpass('Application Secret (hidden): ').strip()}
if any(not values.get(key) or any(c in values[key] for c in ('\r','\n','\x00')) for key in keys):raise SystemExit('All four Enterprise WeChat settings are required.')
url=urlparse(values['WECOM_PUBLIC_URL'])
if not values['WECOM_AGENT_ID'].isdigit() or url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
    raise SystemExit('Invalid AgentID or website root URL.')
values['WECOM_PUBLIC_URL']=values['WECOM_PUBLIC_URL'].rstrip('/')
original=env.read_text(encoding='utf-8-sig')
backup=root/('.env.backup-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
backup.write_text(original,encoding='utf-8');os.chmod(backup,0o600)
lines=[line for line in original.splitlines() if line.strip().partition('=')[0] not in keys]
lines.extend(key+'='+values[key] for key in keys)
env.write_text('\n'.join(lines)+'\n',encoding='utf-8');os.chmod(env,0o600)
print('Enterprise WeChat configuration saved. Other settings preserved. Recreate the application container to apply.')
