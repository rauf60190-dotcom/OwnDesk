from django.db import migrations

# Cross-table ownership cannot be expressed by a CHECK constraint. PostgreSQL
# triggers enforce it even for bulk writes and background workers.
PAIRS = {
    "tickets_ticket": [
        ("conversation_id", "conversations_conversation"),
        ("customer_id", "customers_customer"),
        ("team_id", "teams_team"),
        ("assignee_id", "workspaces_membership"),
        ("previous_ticket_id", "tickets_ticket"),
    ],
    "customers_customer": [("company_id", "customers_company")],
    "customers_customeridentity": [("customer_id", "customers_customer")],
    "integrations_channelconnection": [("default_team_id", "teams_team")],
    "conversations_conversation": [("connection_id", "integrations_channelconnection")],
    "conversations_message": [
        ("conversation_id", "conversations_conversation"),
        ("ticket_id", "tickets_ticket"),
        ("customer_id", "customers_customer"),
    ],
    "attachments_attachment": [("message_id", "conversations_message")],
    "sla_slainstance": [("ticket_id", "tickets_ticket")],
    "notifications_notification": [("ticket_id", "tickets_ticket")],
    "audit_auditevent": [("ticket_id", "tickets_ticket")],
}


def install(apps, editor):
    if editor.connection.vendor != "postgresql":
        return
    editor.execute(
        """
    CREATE FUNCTION desk_scope_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE target_workspace uuid; target_id uuid; i integer;
    BEGIN
      FOR i IN 0..TG_NARGS-1 BY 2 LOOP
        target_id := (to_jsonb(NEW)->>TG_ARGV[i])::uuid;
        IF target_id IS NOT NULL THEN
          EXECUTE format('SELECT workspace_id FROM %I WHERE id = $1', TG_ARGV[i+1]) INTO target_workspace USING target_id;
          IF target_workspace IS DISTINCT FROM NEW.workspace_id THEN
            RAISE EXCEPTION 'Workspace ownership mismatch' USING ERRCODE = '23514';
          END IF;
        END IF;
      END LOOP;
      RETURN NEW;
    END $$;
    CREATE FUNCTION desk_assignee_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.assignee_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM workspaces_membership m JOIN accounts_user u ON u.id=m.user_id
        WHERE m.id=NEW.assignee_id AND m.workspace_id=NEW.workspace_id AND m.active AND u.is_active
          AND m.role IN ('admin','manager','operator') AND (m.role='admin' OR EXISTS (
            SELECT 1 FROM teams_teammember tm WHERE tm.team_id=NEW.team_id AND tm.membership_id=m.id))
      ) THEN RAISE EXCEPTION 'Assignee lacks team access' USING ERRCODE = '23514'; END IF;
      RETURN NEW;
    END $$;
    CREATE FUNCTION desk_team_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF (SELECT workspace_id FROM teams_team WHERE id=NEW.team_id) IS DISTINCT FROM
         (SELECT workspace_id FROM workspaces_membership WHERE id=NEW.membership_id) THEN
        RAISE EXCEPTION 'Team membership ownership mismatch' USING ERRCODE = '23514';
      END IF;
      RETURN NEW;
    END $$;
    CREATE FUNCTION desk_message_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.ticket_id IS NOT NULL AND NEW.conversation_id IS DISTINCT FROM
         (SELECT conversation_id FROM tickets_ticket WHERE id=NEW.ticket_id) THEN
        RAISE EXCEPTION 'Message belongs to a different conversation' USING ERRCODE = '23514';
      END IF;
      RETURN NEW;
    END $$;
    """,
        params=None,
    )
    for table, pairs in PAIRS.items():
        arguments = ",".join("'" + value + "'" for pair in pairs for value in pair)
        editor.execute(
            f"CREATE TRIGGER scope_guard BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION desk_scope_guard({arguments});"
        )
    editor.execute(
        "CREATE TRIGGER assignee_guard BEFORE INSERT OR UPDATE ON tickets_ticket FOR EACH ROW EXECUTE FUNCTION desk_assignee_guard();"
    )
    editor.execute(
        "CREATE TRIGGER team_guard BEFORE INSERT OR UPDATE ON teams_teammember FOR EACH ROW EXECUTE FUNCTION desk_team_guard();"
    )
    editor.execute(
        "CREATE TRIGGER message_guard BEFORE INSERT OR UPDATE ON conversations_message FOR EACH ROW EXECUTE FUNCTION desk_message_guard();"
    )


def remove(apps, editor):
    if editor.connection.vendor != "postgresql":
        return
    for name in ["scope", "assignee", "team", "message"]:
        editor.execute(f"DROP FUNCTION IF EXISTS desk_{name}_guard() CASCADE;")


class Migration(migrations.Migration):
    dependencies = [
        ("tickets", "0001_initial"),
        ("conversations", "0002_initial"),
        ("attachments", "0002_initial"),
        ("sla", "0001_initial"),
        ("notifications", "0001_initial"),
        ("audit", "0002_initial"),
    ]
    operations = [migrations.RunPython(install, remove)]
