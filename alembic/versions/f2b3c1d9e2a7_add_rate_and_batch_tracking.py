"""add_rate_and_batch_tracking

Revision ID: f2b3c1d9e2a7
Revises: 4c7469824631
Create Date: 2026-05-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f2b3c1d9e2a7"
down_revision: Union[str, None] = "4c7469824631"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


batchstatus = sa.Enum("COMPLETED", "FAILED", name="batchstatus")


def upgrade() -> None:
    op.add_column("conversion_jobs", sa.Column("rate_source", sa.String(length=40), nullable=True))
    op.add_column("conversion_jobs", sa.Column("rate_fetched_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("conversion_jobs", sa.Column("rate_error", sa.String(length=500), nullable=True))

    batchstatus.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "conversion_job_batches",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_account_id", sa.UUID(), nullable=False),
        sa.Column("status", batchstatus, nullable=False),
        sa.Column("tokens_total", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("usd_total", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("fee_total", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["conversion_jobs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["token_account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_conversion_job_batches_job_id"), "conversion_job_batches", ["job_id"], unique=False)
    op.create_index(op.f("ix_conversion_job_batches_user_id"), "conversion_job_batches", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_conversion_job_batches_user_id"), table_name="conversion_job_batches")
    op.drop_index(op.f("ix_conversion_job_batches_job_id"), table_name="conversion_job_batches")
    op.drop_table("conversion_job_batches")
    batchstatus.drop(op.get_bind(), checkfirst=True)

    op.drop_column("conversion_jobs", "rate_error")
    op.drop_column("conversion_jobs", "rate_fetched_at")
    op.drop_column("conversion_jobs", "rate_source")
