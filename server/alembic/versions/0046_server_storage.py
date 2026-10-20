"""Server Health storage: a daily disk reading for the projection, and the cached space breakdown."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046_server_storage"
down_revision: Union[str, None] = "0045_server_health"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("server_db_daily", sa.Column("disk_used_bytes", sa.BigInteger(), nullable=True))
    op.add_column("server_db_daily", sa.Column("disk_total_bytes", sa.BigInteger(), nullable=True))
    op.create_table(
        "server_storage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("server_storage")
    op.drop_column("server_db_daily", "disk_total_bytes")
    op.drop_column("server_db_daily", "disk_used_bytes")
