"""Persist repository work and serialize active jobs per repository."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from alembic import op

revision = "004_persistent_jobs"
down_revision = "003_document_generator_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("repo_id", UUID(as_uuid=True), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("lease_token", UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed')", name="ck_jobs_status"),
        sa.CheckConstraint("kind IN ('ingest','scan','embed')", name="ck_jobs_kind"),
    )
    op.create_index(
        "uq_jobs_active_repo", "jobs", ["repo_id"], unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )
    op.create_index("ix_jobs_queue", "jobs", ["status", "created_at"])


def downgrade() -> None:
    op.drop_table("jobs")
