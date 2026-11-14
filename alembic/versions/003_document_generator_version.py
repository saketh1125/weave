"""Version generated summaries and enforce one cache entry per code chunk."""

from alembic import op
import sqlalchemy as sa

revision = "003_document_generator_version"
down_revision = "002_drift_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("generator_version", sa.Text, nullable=True))
    # Retain the latest summary when concurrent older requests created duplicates.
    op.execute("""
        DELETE FROM documents
        WHERE id IN (
            SELECT id FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY chunk_id ORDER BY generated_at DESC, id DESC
                ) AS position
                FROM documents WHERE chunk_id IS NOT NULL
            ) duplicates WHERE position > 1
        )
    """)
    op.create_unique_constraint("uq_documents_chunk_id", "documents", ["chunk_id"])


def downgrade() -> None:
    op.drop_constraint("uq_documents_chunk_id", "documents", type_="unique")
    op.drop_column("documents", "generator_version")
