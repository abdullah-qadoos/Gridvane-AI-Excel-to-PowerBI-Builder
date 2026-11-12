"""Path/plan-only local storage. No cell data or credentials."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("source_path", sa.Text, nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cleaning_plan_json", sa.Text),
        sa.Column("model_plan_json", sa.Text),
        sa.Column("steps_json", sa.Text, nullable=False),
        sa.Column("output_path", sa.Text),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_runs_project_id", "runs", ["project_id"])
    op.create_table(
        "settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
    )


def downgrade():
    op.drop_table("runs")
    op.drop_table("projects")
    op.drop_table("settings")
