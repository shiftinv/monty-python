"""Add hidden field to docs_inventory

Revision ID: d1f327f1548f
Revises: 6a57a6d8d400
Create Date: 2022-05-20 04:59:09.975649

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "d1f327f1548f"
down_revision = "6a57a6d8d400"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column("docs_inventory", sa.Column("hidden", sa.Boolean(), server_default="false", nullable=False))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column("docs_inventory", "hidden")
    # ### end Alembic commands ###
