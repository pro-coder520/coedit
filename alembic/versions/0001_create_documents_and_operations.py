from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("room_id", sa.String(length=255), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column("snapshot_seq", sa.Integer(), nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("room_id"),
    )
    op.create_table(
        "operations",
        sa.Column("room_id", sa.String(length=255), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("counter", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["room_id"], ["documents.room_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("room_id", "seq"),
        sa.UniqueConstraint("room_id", "client_id", "counter", name="uq_operation_client_counter"),
    )


def downgrade() -> None:
    op.drop_table("operations")
    op.drop_table("documents")