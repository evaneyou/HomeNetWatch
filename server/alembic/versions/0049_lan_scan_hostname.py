"""LAN scan hosts carry the name they announce over mDNS."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0049_lan_scan_hostname"
down_revision: Union[str, None] = "0048_lan_scan"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("lan_scan_hosts", sa.Column("hostname", sa.String(length=253), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("lan_scan_hosts", "hostname")
