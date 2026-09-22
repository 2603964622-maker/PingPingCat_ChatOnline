import click
from models.group  import GroupRoleEnum, GroupRole
from models.channel import ChannelRoleEnum, ChannelRole
from exts import db



def init_business_role():
    for role in GroupRoleEnum:
        db.session.add(GroupRole(name=role.name,display_name=role.value))
    for role in ChannelRoleEnum:
        db.session.add(ChannelRole(name=role.name,display_name=role.value))
    db.session.commit()
    click.echo("初始化业务角色权限完成!!!")