"""Read-only configuration check for the native central portal; no secrets in output.

This checks configuration, not Google consent, DNS, SMTP login or email delivery.
Run with the same filesystem access as the portal. No network or database access.
"""
import argparse
import json
from email.utils import parseaddr
from pathlib import Path
import re


def configuration_report(profile, integrations):
    checks = []

    def check(code, ok, instruction):
        checks.append({'code': code, 'ok': bool(ok), 'instruction': instruction})

    check('portal_profile', profile.get('format') == 1 and
          profile.get('public_hostname') == 'owndesk.work',
          'Use the native central portal profile for owndesk.work.')
    check('public_registration', integrations.get('PUBLIC_REGISTRATION_ENABLED') == '1',
          'Enable PUBLIC_REGISTRATION_ENABLED after configuring the providers.')
    check('google_credentials', bool(integrations.get('GOOGLE_CLIENT_ID', '').strip() and
                                    integrations.get('GOOGLE_CLIENT_SECRET', '').strip()),
          'Configure both Google OAuth client credentials in private integrations.json.')
    host = integrations.get('EMAIL_HOST', '')
    check('smtp_host', bool(re.fullmatch(r'(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host)),
          'Configure the SMTP hostname supplied by the mail provider.')
    port = integrations.get('EMAIL_PORT', '587')
    check('smtp_port', port.isascii() and port.isdecimal() and 1 <= int(port) <= 65535,
          'Configure a valid SMTP port; Resend STARTTLS uses 587.')
    tls, ssl = integrations.get('EMAIL_USE_TLS', '1'), integrations.get('EMAIL_USE_SSL', '0')
    check('smtp_encryption', (tls, ssl) in {('1', '0'), ('0', '1')},
          'Enable exactly one SMTP transport: STARTTLS or implicit TLS.')
    user = integrations.get('EMAIL_HOST_USER', '')
    password = integrations.get('EMAIL_HOST_PASSWORD', '')
    check('smtp_credentials', bool(user.strip() and password.strip()),
          'Configure the provider SMTP username and password; never put them in the release archive.')
    sender = integrations.get('DEFAULT_FROM_EMAIL', '')
    address = parseaddr(sender)[1]
    valid_sender = ('\r' not in sender and '\n' not in sender and
                    bool(re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', address)))
    check('sender', valid_sender,
          'Set DEFAULT_FROM_EMAIL to an address on a domain verified by the mail provider.')
    check('mail_worker', profile.get('mail_enabled') is True,
          'Enable the portal mail worker in runtime-profile.json.')
    passed = {item['code']: item['ok'] for item in checks}
    email_codes = ('portal_profile', 'public_registration', 'smtp_host', 'smtp_port',
                   'smtp_encryption', 'smtp_credentials', 'sender', 'mail_worker')
    google_codes = ('portal_profile', 'public_registration', 'google_credentials')
    return {
        'read_only': True,
        'email_configuration_ready': all(passed[code] for code in email_codes),
        'google_signup_configuration_ready': all(passed[code] for code in google_codes),
        'external_acceptance_performed': False,
        'checks': checks,
        'remaining_acceptance': [
            'Check Google callback, requested scopes, branding and login with a separate account.',
            'Verify the sending domain and SMTP credentials with the provider.',
            'Confirm delivery to an agreed mailbox and complete a real one-time registration link.',
            'Check password recovery and reject an already used registration link.',
        ],
    }


def read_object(path):
    # Bound input and reject unexpected types; raw file content/errors must not reach logs.
    with path.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('Configuration too large')
    value = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Expected an object')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True, type=Path,
                        help='Private native portal data directory (not the application folder)')
    args = parser.parse_args(argv)
    try:
        profile = read_object(args.data_dir / 'runtime-profile.json')
        path = args.data_dir / 'integrations.json'
        integrations = read_object(path) if path.exists() else {}
        if any(not isinstance(value, str) for value in integrations.values()):
            raise ValueError('Integration values must be strings')
        report = configuration_report(profile, integrations)
    except (OSError, ValueError, UnicodeError, TypeError, OverflowError, RecursionError):
        print(json.dumps({'read_only': True, 'error': 'configuration_unreadable_or_invalid',
                          'instruction': 'Check access and JSON types in the private portal configuration.'}))
        return 1
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report['email_configuration_ready'] and report['google_signup_configuration_ready'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
