"""Create secrets locally and prompt for private storage credentials."""

import argparse
import base64
import getpass
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit

parser = argparse.ArgumentParser()
parser.add_argument("--domain", required=True)
parser.add_argument("--mode", choices=("organization", "portal"), default="organization")
parser.add_argument("--s3-endpoint")
parser.add_argument("--s3-bucket")
args = parser.parse_args()
if not re.fullmatch(r"[A-Za-z0-9.-]+", args.domain):
    parser.error("Enter a DNS hostname.")
if args.mode == "organization":
    endpoint = urlsplit(args.s3_endpoint or "")
    if endpoint.scheme not in {"http", "https"} or not endpoint.hostname or not args.s3_bucket:
        parser.error("Organization mode requires --s3-endpoint and --s3-bucket.")
elif args.s3_endpoint is not None or args.s3_bucket is not None:
    parser.error("Storage options apply only to organization mode.")
env_path = Path(__file__).resolve().parents[1] / ".env"
if env_path.exists():
    parser.error(".env already exists. Edit it to retain the existing encryption key.")
values = {
    "DOMAIN": args.domain,
    "DEPLOYMENT_MODE": args.mode,
    "PUBLIC_REGISTRATION_ENABLED": "0",
    "LOCAL_EMAIL_PREVIEW": "0",
    "PUBLIC_BASE_URL": "https://" + args.domain,
    "ALLOWED_HOSTS": args.domain + ",backend,localhost",
    "CSRF_TRUSTED_ORIGINS": "https://" + args.domain,
    "SECRET_KEY": secrets.token_urlsafe(64),
    "TOKEN_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "ACCOUNT_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "POSTGRES_PASSWORD": secrets.token_hex(24),
}
if args.mode == "organization":
    values.update(
        S3_ENDPOINT_URL=args.s3_endpoint,
        S3_BUCKET=args.s3_bucket,
        S3_ACCESS_KEY=getpass.getpass("S3 access key: "),
        S3_SECRET_KEY=getpass.getpass("S3 secret key: "),
    )
if any(
    not value or "\n" in value or "\r" in value or "'" in value
    for value in values.values()
):
    parser.error("Every value must be nonempty and free of newlines and single quotes.")
with os.fdopen(
    os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
    "w",
    encoding="utf-8",
    newline="\n",
) as stream:
    for key, value in values.items():
        stream.write(f"{key}='{value}'\n")
print("Created .env. Store a protected backup of SECRET_KEY, TOKEN_ENCRYPTION_KEY and ACCOUNT_ENCRYPTION_KEY.")
