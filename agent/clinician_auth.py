"""Local clinician accounts and revocable sessions, separate from medical events.

Provisioning demo accounts is an explicit development command. No plaintext
password is persisted, and accounts are not automatically created at startup.
"""
from __future__ import annotations
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time
from . import device

ITERATIONS = 600_000
COOKIE = 'care_clinician_session'
IDLE_SECONDS = 30 * 60
MAX_SECONDS = 8 * 60 * 60


def vault_id(root):
    return hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()


def store_path(root):
    directory = Path(os.environ.get('HEALTH_CLINICIAN_AUTH_DIR', device.config_home() / 'clinician-auth'))
    return directory / (vault_id(root) + '.json')


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), ITERATIONS).hex()


def provision(root):
    root = Path(root).resolve()
    from .vault import Vault
    from .demo_metadata import SCENARIO
    vault = Vault.open(root)
    if not any(e.payload.get('demo_scenario') == SCENARIO for e in vault.read().events):
        raise ValueError('Demo accounts can only be provisioned for the Jane demonstration record.')
    path = store_path(root)
    if path.exists():
        raise ValueError('Accounts already exist. Passwords cannot be recovered; remove the account file explicitly to reprovision.')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    accounts = {}
    credentials = {}
    for username, name, role, salutation in [
        ('james.smith', 'Dr James Smith', 'Usual GP', 'Dr Smith'),
        ('olivia.bennett', 'Dr Olivia Bennett', 'Locum GP', 'Dr Bennett'),
    ]:
        password = secrets.token_urlsafe(18)
        salt = secrets.token_hex(16)
        accounts[username] = {'username':username, 'name':name, 'role':role,
            'salutation':salutation, 'patients':[vault_id(root)], 'development_only':True,
            'password':{'algorithm':'pbkdf2-sha256', 'iterations':ITERATIONS,
                        'salt':salt, 'hash':password_hash(password, salt)}}
        credentials[username] = password
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        json.dump({'version':1, 'accounts':accounts}, handle, indent=2)
    return credentials


class AuthStore:
    def __init__(self, root, clock=time.monotonic):
        self.patient = vault_id(root)
        self.clock = clock
        self.lock = threading.RLock()
        self.sessions = {}
        self.failures = {}
        path = store_path(root)
        self.accounts = json.loads(path.read_text())['accounts'] if path.exists() else {}
        self.dummy_salt = secrets.token_hex(16)
        self.dummy_hash = password_hash(secrets.token_urlsafe(32), self.dummy_salt)

    @staticmethod
    def public(account):
        return {key:account[key] for key in ('username', 'name', 'role', 'salutation')}

    def login(self, username, password, address, previous=None):
        now = self.clock()
        with self.lock:
            # Bound memory and retain no submitted credentials in the limiter.
            self.failures = {ip:[t for t in times if now-t < 900]
                             for ip,times in self.failures.items() if any(now-t < 900 for t in times)}
            if len(self.failures.get(address, [])) >= 5:
                return None, 'limited'
            account = self.accounts.get(username.casefold())
            saved = account['password'] if account else {'salt':self.dummy_salt, 'hash':self.dummy_hash}
            matched = hmac.compare_digest(password_hash(password, saved['salt']), saved['hash'])
            if not account or not matched or self.patient not in account.get('patients', []):
                self.failures.setdefault(address, []).append(now)
                return None, 'invalid'
            self.failures.pop(address, None)
            self.revoke(previous)
            self.sessions = {key:value for key,value in self.sessions.items()
                             if now-value['used'] < IDLE_SECONDS and now-value['created'] < MAX_SECONDS}
            token = secrets.token_urlsafe(32)
            session = {'account':account, 'created':now, 'used':now, 'csrf':secrets.token_urlsafe(32)}
            self.sessions[hashlib.sha256(token.encode()).hexdigest()] = session
            return (token, session), None

    def resolve(self, token, touch=True):
        if not token or len(token)>128:
            return None
        with self.lock:
            key = hashlib.sha256(token.encode()).hexdigest()
            session = self.sessions.get(key)
            now = self.clock()
            if not session:
                return None
            if (now-session['used'] >= IDLE_SECONDS or now-session['created'] >= MAX_SECONDS
                    or self.patient not in session['account'].get('patients', [])):
                self.sessions.pop(key, None)
                return None
            if touch:
                session['used'] = now
            return session

    def revoke(self, token):
        if token:
            with self.lock:
                self.sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Provision development-only accounts for the Jane demo.')
    parser.add_argument('command', choices=['provision-demo'])
    parser.add_argument('--vault', required=True)
    args = parser.parse_args()
    print(json.dumps(provision(args.vault)))
