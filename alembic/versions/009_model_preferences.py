"""Persist single-user model selections."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "009_model_preferences"
down_revision = "008_documentation_quotas"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "model_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workflows", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("antigravity_model", sa.Text(), nullable=True),
        sa.Column("antigravity_verified_model", sa.Text(), nullable=True),
        sa.Column("antigravity_identity", sa.Text(), nullable=True),
        sa.CheckConstraint("id = 1", name="model_preferences_singleton"),
    )


def downgrade():
    op.drop_table("model_preferences")
