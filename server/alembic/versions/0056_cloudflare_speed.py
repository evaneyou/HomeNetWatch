"""Settings for the Cloudflare Speed Test: on or off, and how often it runs."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0056_cloudflare_speed"
down_revision: Union[str, None] = "0055_kpi_valid_hours"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "internet_service_settings",
        sa.Column("cloudflare_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "internet_service_settings",
        sa.Column("cloudflare_interval_seconds", sa.Integer(), nullable=False, server_default=sa.text("3600")),
    )


def downgrade() -> None:
    op.drop_column("internet_service_settings", "cloudflare_interval_seconds")
    op.drop_column("internet_service_settings", "cloudflare_enabled")
