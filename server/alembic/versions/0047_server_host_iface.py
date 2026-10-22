"""Server Health records the machine NIC separately from the app container NIC."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0047_server_host_iface"
down_revision: Union[str, None] = "0046_server_storage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("server_samples", sa.Column("host_iface", sa.String(length=32), nullable=True))
    op.add_column("server_samples", sa.Column("host_rx_bytes", sa.BigInteger(), nullable=True))
    op.add_column("server_samples", sa.Column("host_tx_bytes", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("server_samples", "host_tx_bytes")
    op.drop_column("server_samples", "host_rx_bytes")
    op.drop_column("server_samples", "host_iface")
