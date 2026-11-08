"""A session version per user, so signing out or a new password ends every session of that account."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0053_session_version"
down_revision: Union[str, None] = "0052_installer_pairing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("session_version", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("users", "session_version")
