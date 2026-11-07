"""Generalize persisted decision adapters without changing provider bindings (Feature 993)."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_model_provider_adapter", "model_provider", type_="check")
    op.execute(
        "UPDATE model_provider SET adapter='systemone_decision' WHERE adapter='aliyun_decision'"
    )
    op.create_check_constraint(
        "ck_model_provider_adapter",
        "model_provider",
        "adapter IN ('litellm_chat','systemone_decision')",
    )


def downgrade():
    raise RuntimeError(
        "Restore a verified pre-upgrade backup to a new owned database; model records are retained."
    )
