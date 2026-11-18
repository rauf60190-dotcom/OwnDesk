"""One-time logical import into an empty managed database, before migrations or HTTP."""
import hashlib
import os
import re
import subprocess

from native_runtime import ROOT


def initial_restore(data, profile, secret, env):
    restore = profile.get('initial_restore')
    if restore is None:
        return
    if (not isinstance(restore, dict) or restore.get('file') != 'initial-restore.dump'
            or not re.fullmatch('[a-f0-9]{64}', restore.get('sha256', ''))):
        raise ValueError('Invalid initial database restore profile')
    counts = restore.get('counts')
    if not isinstance(counts, dict) or not counts or any(
        not re.fullmatch('[a-z][a-z0-9_]{0,62}', k) or type(v) is not int or v < 0 for k, v in counts.items()):
        raise ValueError('Invalid restore table counts')
    pg = profile['postgres']
    import psycopg
    with psycopg.connect(host='127.0.0.1', port=pg['port'], dbname=pg['database'], user=pg['role'],
                          password=secret['POSTGRES_APP_PASSWORD'], autocommit=True) as conn:
        receipt = conn.execute("SELECT to_regclass('public._owndesk_restore_receipt')").fetchone()[0]
        if receipt:
            if conn.execute('SELECT 1 FROM public._owndesk_restore_receipt WHERE sha256=%s', (restore['sha256'],)).fetchone():
                return  # The live database can evolve after the first successful import.
            raise RuntimeError('Database has another restore receipt; refusing replacement')
        if conn.execute("SELECT 1 FROM pg_tables WHERE schemaname='public' LIMIT 1").fetchone():
            raise RuntimeError('Initial restore requires an empty database')
    archive = data / restore['file']
    if archive.is_symlink():
        raise ValueError('Initial restore archive must not be a symbolic link')
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != restore['sha256']:
        raise ValueError('Initial restore archive checksum does not match')
    suffix = '.exe' if os.name == 'nt' else ''
    binaries = (ROOT / pg['bin']).resolve()
    sqlfile = data / 'initial-restore.sql'
    receipt_sql = "CREATE TABLE public._owndesk_restore_receipt (sha256 char(64) PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());"
    for table, count in counts.items():
        receipt_sql += (f'DO $$ BEGIN IF (SELECT count(*) FROM public."{table}") <> {count} '
                        f"THEN RAISE EXCEPTION 'Restored row count differs: {table}'; END IF; END $$;")
    receipt_sql += f"INSERT INTO public._owndesk_restore_receipt(sha256) VALUES ('{restore['sha256']}');"
    database_env = {k: v for k, v in env.items() if not k.startswith('PG')}
    database_env.update(PGHOST='127.0.0.1', PGPORT=str(pg['port']), PGDATABASE=pg['database'],
                        PGUSER=pg['role'], PGPASSWORD=secret['POSTGRES_APP_PASSWORD'], PGCONNECT_TIMEOUT='10')
    def command(args, command_env):
        # Use explicit handles: Windows services and PowerShell redirects can have invalid inherited handles.
        with (data / 'initial-restore.log').open('ab') as log:
            completed = subprocess.run([str(x) for x in args], env=command_env, cwd=ROOT,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if completed.returncode:
            raise RuntimeError('Initial database restore failed; inspect private initial-restore.log')
    try:
        command([binaries / ('pg_restore' + suffix), '--no-owner', '--no-privileges', '--file', sqlfile, archive], database_env)
        # The import, row-count checks and receipt commit together, including after a power loss.
        command([binaries / ('psql' + suffix), '-X', '-w', '--single-transaction', '-v', 'ON_ERROR_STOP=1',
                 '-f', sqlfile, '-c', receipt_sql], database_env)
    finally:
        sqlfile.unlink(missing_ok=True)
