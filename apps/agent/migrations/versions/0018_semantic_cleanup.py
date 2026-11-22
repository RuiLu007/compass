"""Keep the admitted Run snapshot as the single source of its registry pins."""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        LOCK TABLE semantic_run_binding IN SHARE MODE;
        DO $guard$
        BEGIN
            IF EXISTS (
                SELECT FROM semantic_run_binding b JOIN agent_run r ON r.id=b.run_id
                WHERE (r.config_snapshot #> '{contextRuntime,registry,manifest}')
                    IS DISTINCT FROM to_jsonb(b.manifest_hash)
                   OR (r.config_snapshot #> '{contextRuntime,registry,pins}')
                    IS DISTINCT FROM b.partition_releases
            ) THEN
                RAISE EXCEPTION 'Registry binding differs from Run snapshot';
            END IF;
        END
        $guard$
    """)
    op.drop_table("semantic_run_binding")


def downgrade() -> None:
    raise RuntimeError("Restore the verified agent/main backup to recover pre-upgrade records.")
