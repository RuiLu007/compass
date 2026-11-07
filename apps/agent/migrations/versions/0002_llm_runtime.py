"""Model configuration and invocation metadata (Feature 993)."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "model_provider",
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("catalog_id", sa.String(length=200), nullable=False, primary_key=False),
        sa.Column("vendor", sa.String(length=200), nullable=False, primary_key=False),
        sa.Column("api_model", sa.String(length=200), nullable=False, primary_key=False),
        sa.Column("sdk_model", sa.String(length=200), nullable=True, primary_key=False),
        sa.Column("alias", sa.String(length=80), nullable=False, primary_key=False),
        sa.Column("kind", sa.String(length=20), nullable=False, primary_key=False),
        sa.Column("adapter", sa.String(length=30), nullable=False, primary_key=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False, primary_key=False),
        sa.Column("api_key_ciphertext", sa.Text(), nullable=False, primary_key=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, primary_key=False),
        sa.Column("chat_visible", sa.Boolean(), nullable=False, primary_key=False),
        sa.Column("version", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("binding_version", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False, primary_key=False),
        sa.CheckConstraint(
            "kind = 'llm' OR chat_visible = false", name="ck_model_provider_chat_kind"
        ),
        sa.CheckConstraint(
            "adapter IN ('litellm_chat','aliyun_decision')", name="ck_model_provider_adapter"
        ),
        sa.CheckConstraint(
            "version > 0 AND binding_version > 0 AND binding_version <= version",
            name="ck_model_provider_version",
        ),
        sa.CheckConstraint("kind IN ('llm','decision')", name="ck_model_provider_kind"),
    )
    op.create_index(
        "uq_model_provider_alias", "model_provider", [sa.text("lower(trim(alias))")], unique=True
    )
    op.create_table(
        "model_change",
        sa.Column("operation_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("provider_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("request_mac", sa.String(length=64), nullable=False, primary_key=False),
        sa.Column("before_version", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("after_version", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("changed_fields", sa.JSON(), nullable=False, primary_key=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.ForeignKeyConstraint(["provider_id"], ["model_provider.id"]),
    )
    op.create_index(
        "ix_model_change_provider_time",
        "model_change",
        ["provider_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "model_call",
        sa.Column("call_id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("provider_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("config_version", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("binding_version", sa.Integer(), nullable=False, primary_key=False),
        sa.Column("requested_model", sa.String(length=200), nullable=False, primary_key=False),
        sa.Column("response_model", sa.String(length=200), nullable=True, primary_key=False),
        sa.Column("purpose", sa.String(length=20), nullable=False, primary_key=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False, primary_key=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=True, primary_key=False),
        sa.Column("capability_summary", sa.JSON(), nullable=False, primary_key=False),
        sa.Column("status", sa.String(length=20), nullable=False, primary_key=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True, primary_key=False),
        sa.Column("usage", sa.JSON(), nullable=True, primary_key=False),
        sa.Column("error_code", sa.String(length=80), nullable=True, primary_key=False),
        sa.Column("upstream_request_id", sa.String(length=200), nullable=True, primary_key=False),
        sa.Column("upstream_outcome_unknown", sa.Boolean(), nullable=False, primary_key=False),
        sa.CheckConstraint(
            "(purpose = 'test' AND tenant_id IS NULL AND workspace_id IS NULL) "
            "OR (purpose = 'chat' AND tenant_id IS NOT NULL AND workspace_id IS NOT NULL)",
            name="ck_model_call_scope",
        ),
        sa.ForeignKeyConstraint(["provider_id"], ["model_provider.id"]),
        sa.CheckConstraint("purpose IN ('test','chat')", name="ck_model_call_purpose"),
        sa.CheckConstraint(
            "status IN ('running','completed','failed','canceled','incomplete')",
            name="ck_model_call_status",
        ),
    )
    op.create_index(
        "ix_model_call_actor_time",
        "model_call",
        [
            "tenant_id",
            "actor_user_id",
            "started_at",
        ],
        unique=False,
    )
    op.create_index(
        "ix_model_call_provider_time",
        "model_call",
        [
            "provider_id",
            "purpose",
            "started_at",
        ],
        unique=False,
    )


def downgrade():
    raise RuntimeError(
        "Restore a verified backup to a new owned database; model records are retained."
    )
