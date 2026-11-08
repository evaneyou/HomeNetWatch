"""The address the server resolved an on-demand trace target to, so the agent never resolves the name again."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0054_trace_address"
down_revision: Union[str, None] = "0053_session_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("adhoc_traces", sa.Column("address", sa.String(length=45), nullable=True))


def downgrade() -> None:
    op.drop_column("adhoc_traces", "address")
