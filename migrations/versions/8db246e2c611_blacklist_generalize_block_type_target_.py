"""blacklist generalize block_type target_id

Revision ID: 8db246e2c611
Revises: 11b7e3868c8d
Create Date: 2026-10-01 20:59:15.290226

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '8db246e2c611'
down_revision = '11b7e3868c8d'
branch_labels = None
depends_on = None


def upgrade():
    # ### 手动编排：新列先可空/带默认，回填旧数据后再收紧，避免 MySQL 对存量行报错 ###
    # 1) 新增 block_type（默认 user）与 target_id（先可空）
    op.add_column('blacklists', sa.Column('block_type', sa.String(length=16), nullable=False,
                                          server_default='user', comment='拉黑对象类型：user|group|channel'))
    op.add_column('blacklists', sa.Column('target_id', sa.Integer(), nullable=True,
                                          comment='被拉黑的用户/群/频道ID'))
    # 2) 回填：旧的 blocked_id（用户）迁移为 target_id，block_type 已是默认 'user'
    op.execute("UPDATE blacklists SET target_id = blocked_id WHERE target_id IS NULL")
    # 3) target_id 收紧为非空
    op.alter_column('blacklists', 'target_id', existing_type=sa.Integer(), nullable=False,
                    existing_comment='被拉黑的用户/群/频道ID')
    # 4) 建立新的联合唯一约束
    op.create_unique_constraint('uk_blacklist', 'blacklists', ['user_id', 'block_type', 'target_id'])
    # 5) 删除旧的 blocked_id 外键与列
    with op.batch_alter_table('blacklists', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('blacklists_ibfk_1'), type_='foreignkey')
        batch_op.drop_column('blocked_id')
    # ### end Alembic commands ###


def downgrade():
    # ### 回退：重建 blocked_id（可空，仅回填 user 类型），删除泛化列 ###
    op.add_column('blacklists', sa.Column('blocked_id', mysql.INTEGER(), autoincrement=False,
                                          nullable=True, comment='被拉黑者'))
    op.execute("UPDATE blacklists SET blocked_id = target_id WHERE block_type = 'user'")
    with op.batch_alter_table('blacklists', schema=None) as batch_op:
        batch_op.drop_constraint('uk_blacklist', type_='unique')
        batch_op.create_foreign_key(batch_op.f('blacklists_ibfk_1'), 'users', ['blocked_id'], ['id'])
        batch_op.drop_column('target_id')
        batch_op.drop_column('block_type')
    # ### end Alembic commands ###
