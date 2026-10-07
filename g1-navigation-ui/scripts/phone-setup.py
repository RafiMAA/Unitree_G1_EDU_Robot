#!/usr/bin/env python3
"""Create local HTTPS certificates; the phone must explicitly trust the CA."""
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess

root = Path(__file__).resolve().parents[1]
folder = root / '.phone-tls'
if not shutil.which('openssl'):
    raise SystemExit('Install openssl before running phone:setup.')
folder.mkdir(mode=0o700, exist_ok=True)
os.chmod(folder, 0o700)
ips = {'127.0.0.1', '::1'}
result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, check=True)
for address in result.stdout.split():
    try:
        ips.add(str(ipaddress.ip_address(address)))
    except ValueError:
        pass
names = {'localhost', socket.gethostname()}
ca_key, ca_cert = folder / 'ca.key', folder / 'ca.crt'
if not (ca_key.exists() and ca_cert.exists()):
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '365', '-keyout', str(ca_key), '-out', str(ca_cert), '-subj', '/CN=G1 Console Local CA', '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign'], check=True, capture_output=True)
ext = folder / 'server.ext'
ext.write_text('basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=' + ','.join([f'DNS:{name}' for name in sorted(names)] + [f'IP:{ip}' for ip in sorted(ips)]) + '\n')
subprocess.run(['openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(folder / 'server.key'), '-out', str(folder / 'server.csr'), '-subj', '/CN=G1 Navigation Console'], check=True, capture_output=True)
subprocess.run(['openssl', 'x509', '-req', '-in', str(folder / 'server.csr'), '-CA', str(ca_cert), '-CAkey', str(ca_key), '-CAcreateserial', '-out', str(folder / 'server.crt'), '-days', '365', '-extfile', str(ext)], check=True, capture_output=True)
for name in ('ca.key', 'server.key'):
    os.chmod(folder / name, 0o600)
(folder / 'hosts.json').write_text(json.dumps(sorted(names | ips)))
print('HTTPS configured. Restart npm run dev.')
print('Install and trust ONLY .phone-tls/ca.crt on your phone; never copy the private .key files.')
print('Connect both devices to the same trusted Wi-Fi, then open one of:')
for ip in sorted(ips - {'127.0.0.1', '::1'}):
    address = f'[{ip}]' if ':' in ip else ip
    print(f'  https://{address}:5173')
print('If your computer IP changes, run phone:setup again.')
