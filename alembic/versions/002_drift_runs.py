"""Store documentation alignment runs independently of indexed chunks."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "002_drift_runs"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drift_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("repo_id", UUID(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("commit_sha", sa.Text, nullable=True),
        sa.Column("result", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_drift_runs_repo_id", "drift_runs", ["repo_id"])


def downgrade() -> None:
    op.drop_table("drift_runs")
