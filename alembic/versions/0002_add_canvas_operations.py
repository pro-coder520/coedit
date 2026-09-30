import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "canvas_documents",
        sa.Column("room_id", sa.String(length=255), nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("room_id"),
    )
    op.create_table(
        "canvas_operations",
        sa.Column("room_id", sa.String(length=255), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("counter", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["room_id"], ["canvas_documents.room_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("room_id", "seq"),
        sa.UniqueConstraint(
            "room_id", "client_id", "counter", name="uq_canvas_operation_client_counter"
        ),
    )


def downgrade() -> None:
    op.drop_table("canvas_operations")
    op.drop_table("canvas_documents")