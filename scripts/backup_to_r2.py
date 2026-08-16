#!/usr/bin/env python3
"""Nightly encrypted database backup to Cloudflare R2.

Supabase's free tier has no automated backups and no point-in-time recovery, so without this a bad
migration or a lost project is unrecoverable. This is the layer that leaves the machine.

What it does:
  1. pg_dump the whole database (custom format, compressed)
  2. encrypt it with GPG symmetric AES256 before it leaves this process
  3. upload to R2 as immigroov-YYYY-MM-DD.dump.gpg
  4. delete anything older than BACKUP_RETENTION_DAYS

Encryption is not optional. The dump holds customer emails, phone numbers and payment records, and R2
is a third party. Encrypting locally means the bucket never contains readable personal data, which is
also what keeps this defensible under GDPR.

Required env:
  DATABASE_URL              the database to dump
  R2_ACCOUNT_ID             Cloudflare account id
  R2_ACCESS_KEY_ID          S3-compatible key (NOT the cfat_ API token)
  R2_SECRET_ACCESS_KEY      S3-compatible secret
  R2_BUCKET                 bucket name
  BACKUP_PASSPHRASE         GPG symmetric passphrase. LOSE THIS AND THE BACKUPS ARE UNREADABLE.

Optional:
  BACKUP_RETENTION_DAYS     default 30

Restore:
  gpg --decrypt --batch --passphrase "$BACKUP_PASSPHRASE" immigroov-2026-08-16.dump.gpg > db.dump
  pg_restore --clean --if-exists -d "$TARGET_DATABASE_URL" db.dump
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("backup")

RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))


def _require(*names: str) -> dict[str, str]:
    vals, missing = {}, []
    for n in names:
        v = os.getenv(n)
        if not v:
            missing.append(n)
        vals[n] = v or ""
    if missing:
        log.error("missing env: %s", ", ".join(missing))
        raise SystemExit(1)
    return vals


def _run(cmd: list[str], **kw) -> None:
    """Run a command, surfacing stderr on failure. Never logs the command, since several carry
    credentials in argv."""
    p = subprocess.run(cmd, capture_output=True, **kw)
    if p.returncode != 0:
        log.error("%s failed: %s", cmd[0], p.stderr.decode("utf-8", "replace")[:400])
        raise SystemExit(1)


def main() -> int:
    env = _require("DATABASE_URL", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID",
                   "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "BACKUP_PASSPHRASE")

    try:
        import boto3
    except ImportError:
        log.error("boto3 not installed. add it to requirements.txt")
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"immigroov-{stamp}.dump.gpg"

    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "db.dump"
        enc = Path(tmp) / key

        # -Fc is the custom format: compressed, and pg_restore can pick individual tables out of it,
        # which matters when you need one table back rather than the whole database.
        log.info("dumping database")
        _run(["pg_dump", "--no-owner", "--no-privileges", "-Fc", "-f", str(dump), env["DATABASE_URL"]])
        raw_mb = dump.stat().st_size / 1024 / 1024
        log.info("dump complete: %.1f MB", raw_mb)

        log.info("encrypting")
        _run(["gpg", "--batch", "--yes", "--symmetric", "--cipher-algo", "AES256",
              "--passphrase-fd", "0", "-o", str(enc), str(dump)],
             input=env["BACKUP_PASSPHRASE"].encode())
        log.info("encrypted: %.1f MB", enc.stat().st_size / 1024 / 1024)

        s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{env['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
            aws_access_key_id=env["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=env["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
        )
        log.info("uploading %s", key)
        s3.upload_file(str(enc), env["R2_BUCKET"], key)

        # Prune. Done after a successful upload, never before, so a failed run cannot leave you with
        # fewer backups than you started with.
        cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        removed = 0
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=env["R2_BUCKET"]):
            for obj in page.get("Contents", []):
                if obj["Key"] == key:
                    continue
                if obj["LastModified"] < cutoff:
                    s3.delete_object(Bucket=env["R2_BUCKET"], Key=obj["Key"])
                    removed += 1
        kept = sum(len(p.get("Contents", [])) for p in paginator.paginate(Bucket=env["R2_BUCKET"]))
        log.info("done. pruned %d older than %d days, %d backup(s) retained", removed, RETENTION_DAYS, kept)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
