"""Initialize the private data volume and an application role without server privileges."""
import os
import psycopg
from psycopg import sql

os.chown("/data", 10001, 10001)
os.chmod("/data", 0o700)
with psycopg.connect(host="db", dbname="owndesk", user="owndesk_admin",
                     password=os.environ["POSTGRES_ADMIN_PASSWORD"], autocommit=True) as connection:
    role = connection.execute("SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname='owndesk'").fetchone()
    if role is None:
        connection.execute(sql.SQL("CREATE ROLE owndesk LOGIN PASSWORD {}").format(sql.Literal(os.environ["POSTGRES_APP_PASSWORD"])))
    elif any(role):
        raise RuntimeError("The existing application role has unexpected server privileges; refusing to continue")
    connection.execute("ALTER DATABASE owndesk OWNER TO owndesk")
print("Private volume and restricted application database role are ready.")
