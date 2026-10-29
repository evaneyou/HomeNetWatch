"""Monitor platform: a Raspberry Pi runs every check, an Android device only the speed test and synthetic traffic."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0051_monitor_platform"
down_revision: Union[str, None] = "0050_gemini_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("monitors", sa.Column("platform", sa.String(length=16), nullable=False, server_default="pi"))
    op.add_column("enrollment_checkins", sa.Column("platform", sa.String(length=16), nullable=False, server_default="pi"))
    op.add_column("enrollment_checkins", sa.Column("device_name", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("enrollment_checkins", "device_name")
    op.drop_column("enrollment_checkins", "platform")
    op.drop_column("monitors", "platform")
