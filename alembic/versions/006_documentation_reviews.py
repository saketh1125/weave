"""Persist documentation reviews and immutable indexed snapshots."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
revision = '006_documentation_reviews'
down_revision = '005_login_buckets'
branch_labels = None
depends_on = None

def upgrade():
    op.drop_constraint('ck_jobs_kind', 'jobs', type_='check')
    op.create_check_constraint('ck_jobs_kind', 'jobs', "kind IN ('ingest','scan','embed','documentation_review')")
    op.create_table('documentation_reviews',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('repo_id', UUID(as_uuid=True), sa.ForeignKey('repositories.id', ondelete='CASCADE'), nullable=False),
        sa.Column('job_id', UUID(as_uuid=True), sa.ForeignKey('jobs.id', ondelete='SET NULL')),
        sa.Column('commit_sha', sa.Text, nullable=False), sa.Column('status', sa.Text, nullable=False),
        sa.Column('model', sa.Text, nullable=False), sa.Column('snapshot', JSONB, nullable=False),
        sa.Column('manifest', JSONB, nullable=False), sa.Column('reason', sa.Text),
        sa.Column('retry_after_seconds', sa.Integer), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('queued','running','paused','completed','failed','stale')", name='ck_documentation_reviews_status'))
    op.create_index('ix_documentation_reviews_repo_id', 'documentation_reviews', ['repo_id'])
    op.create_table('documentation_review_tasks',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('run_id', UUID(as_uuid=True), sa.ForeignKey('documentation_reviews.id', ondelete='CASCADE'), nullable=False),
        sa.Column('path', sa.Text, nullable=False), sa.Column('start_line', sa.Integer, nullable=False),
        sa.Column('end_line', sa.Integer, nullable=False), sa.Column('content', sa.Text, nullable=False),
        sa.Column('status', sa.Text, nullable=False), sa.Column('state', JSONB, nullable=False), sa.Column('assessments', JSONB, nullable=False),
        sa.UniqueConstraint('run_id', 'path', 'start_line', 'end_line', name='uq_documentation_review_task_range'),
        sa.CheckConstraint('start_line >= 1 AND end_line >= start_line', name='ck_documentation_review_task_range'),
        sa.CheckConstraint("status IN ('pending','completed','failed')", name='ck_documentation_review_task_status'))
    op.create_index('ix_documentation_review_tasks_run_id', 'documentation_review_tasks', ['run_id'])

def downgrade():
    op.drop_table('documentation_review_tasks')
    op.drop_table('documentation_reviews')
    op.drop_constraint('ck_jobs_kind', 'jobs', type_='check')
    op.create_check_constraint('ck_jobs_kind', 'jobs', "kind IN ('ingest','scan','embed')")
