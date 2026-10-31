"""Installer codes can carry the pairing token from an Android device's QR code."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0052_installer_pairing"
down_revision: Union[str, None] = "0051_monitor_platform"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("installer_codes", sa.Column("pairing_hash", sa.String(length=64), nullable=True))
    op.create_index("ix_installer_codes_pairing_hash", "installer_codes", ["pairing_hash"])


def downgrade() -> None:
    op.drop_index("ix_installer_codes_pairing_hash", table_name="installer_codes")
    op.drop_column("installer_codes", "pairing_hash")
