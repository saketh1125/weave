"""Share documentation provider reservations across worker processes."""
from alembic import op
import sqlalchemy as sa

revision = "008_documentation_quotas"
down_revision = "007_documentation_drafts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "documentation_provider_quotas",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("next_call_at", sa.Float(), nullable=False, server_default="0"),
        sa.Column("blocked_until", sa.Float(), nullable=False, server_default="0"),
        sa.Column("window_start", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_requests", sa.Integer(), nullable=False, server_default="0"),
    )

    quota = sa.table("documentation_provider_quotas", sa.column("id", sa.Text()))
    op.bulk_insert(quota, [{"id": "mistral:1"}, {"id": "mistral:2"}])


def downgrade():
    op.drop_table("documentation_provider_quotas")
