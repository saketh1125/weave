"""Persist approved documentation publication state."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
revision = '007_documentation_drafts'
down_revision = '006_documentation_reviews'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('documentation_drafts',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('repo_id', UUID(as_uuid=True), sa.ForeignKey('repositories.id', ondelete='CASCADE'), nullable=False),
        sa.Column('review_id', UUID(as_uuid=True), sa.ForeignKey('documentation_reviews.id', ondelete='CASCADE'), nullable=False),
        sa.Column('base_commit', sa.Text, nullable=False), sa.Column('draft_hash', sa.Text, nullable=False),
        sa.Column('files', JSONB, nullable=False), sa.Column('diff', sa.Text, nullable=False),
        sa.Column('status', sa.Text, nullable=False), sa.Column('branch', sa.Text), sa.Column('pr_url', sa.Text), sa.Column('publication_commit', sa.Text),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('draft','publishing','published')", name='ck_documentation_drafts_status'))
    op.create_index('ix_documentation_drafts_repo_id', 'documentation_drafts', ['repo_id'])

def downgrade():
    op.drop_table('documentation_drafts')

