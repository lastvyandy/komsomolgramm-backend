#!/usr/bin/env python3
"""Add TURN configuration without printing secrets or replacing existing settings."""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import secrets

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--host', required=True, help='Public DNS name or IPv4, without a URL scheme')
parser.add_argument('--external-ip', required=True, help='Public VPS IPv4')
args = parser.parse_args()
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', args.host):
    parser.error('Invalid host; use a DNS name or IPv4 without ports')
try:
    address = ipaddress.IPv4Address(args.external_ip)
    if not address.is_global:
        parser.error('external-ip must be a public IPv4')
except ipaddress.AddressValueError:
    parser.error('Invalid public IPv4')
path = Path(__file__).resolve().parents[1] / '.env'
if not path.is_file():
    parser.error('Create the existing API .env first; this helper only adds call settings')
text = path.read_text()
values = {'CALL_TURN_HOST': args.host, 'CALL_TURN_EXTERNAL_IP': str(address),
          'CALL_TURN_SECRET': secrets.token_urlsafe(48)}
for key, value in values.items():
    match = re.search(rf'^{key}=(.*)$', text, re.MULTILINE)
    if match and match[1].strip().strip('\"\x27'):
        continue
    if match:
        text = text[:match.start()] + f'{key}={value}' + text[match.end():]
    else:
        text = text.rstrip('\n') + f'\n{key}={value}\n'
# Atomic replacement with private permissions; no intermediate readable secret file.
temp = path.with_name('.env.calls.tmp')
fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    with os.fdopen(fd, 'w') as output:
        output.write(text); output.flush(); os.fsync(output.fileno())
    os.replace(temp, path)
finally:
    if temp.exists():
        temp.unlink()
print('TURN configuration saved. Existing non-empty settings were preserved; no secrets printed.')
