"""Persist login throttling across API workers and restarts."""
import sqlalchemy as sa
from alembic import op

revision = "005_login_buckets"
down_revision = "004_persistent_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_buckets",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("login_buckets")
