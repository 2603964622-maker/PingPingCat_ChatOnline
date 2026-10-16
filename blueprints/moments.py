from flask import Blueprint, request, g
import datetime

from decorators import login_required
from exts import db, socketio
from models.moment import Moment, MomentMedia, MomentLike, MomentComment
from models.user import User
from models.contact import friends, Blacklist
from utils import resp_json, save_upload_file, guard_sensitive, is_user_muted, delete_old_file
from utils import PARAMS_ERROR, OK, SERVER_ERROR

bp = Blueprint('moments', __name__, url_prefix='/moment')


def _blocked_pair_ids(me):
    """与我存在拉黑关系（双向）的用户 id 集合：动态流互相不可见"""
    out = {r.target_id for r in Blacklist.query.filter_by(user_id=me, block_type='user').all()}
    out |= {r.user_id for r in Blacklist.query.filter_by(target_id=me, block_type='user').all()}
    return out


def _friend_ids(me):
    rows = db.session.query(friends.c.friend_id).filter(friends.c.user_id == me).all()
    return {r[0] for r in rows}


def _media_map(moment_ids):
    """批量取媒体，按 moment_id 分组，避免 N+1"""
    out = {}
    if not moment_ids:
        return out
    for m in MomentMedia.query.filter(MomentMedia.moment_id.in_(moment_ids)).order_by(MomentMedia.sort_order, MomentMedia.id).all():
        out.setdefault(m.moment_id, []).append({"url": m.url, "media_type": m.media_type})
    return out


def _comment_map(moment_ids):
    """批量取各动态的评论列表（feed 常驻展示，避免逐条请求的 N+1）"""
    out = {}
    if not moment_ids:
        return out
    comments = MomentComment.query.filter(MomentComment.moment_id.in_(moment_ids)) \
        .order_by(MomentComment.id).all()
    uids = {c.user_id for c in comments}
    users = {u.id: u for u in User.query.filter(User.id.in_(uids)).all()} if uids else {}
    by_id = {c.id: c for c in comments}
    for c in comments:
        u = users.get(c.user_id)
        # 回复链：取被回复评论的作者昵称（同属本批评论）
        reply_nick = None
        if c.reply_to:
            tgt = by_id.get(c.reply_to)
            if tgt:
                tu = users.get(tgt.user_id)
                reply_nick = tu.nickname if tu else None
        out.setdefault(c.moment_id, []).append({
            "comment_id": c.id,
            "user_id": c.user_id,
            "user_nickname": u.nickname if u else f"用户 {c.user_id}",
            "user_avatar": u.avatar if u else None,
            "content": c.content,
            "reply_to": c.reply_to,
            "reply_to_nickname": reply_nick,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })
    return out


def _liker_map(moment_ids):
    """批量取各动态的点赞人昵称（按点赞先后排序，卡片常驻展示）"""
    out = {}
    if not moment_ids:
        return out
    rows = db.session.query(MomentLike.moment_id, User.nickname).join(
        User, User.id == MomentLike.user_id
    ).filter(MomentLike.moment_id.in_(moment_ids)).order_by(MomentLike.id).all()
    for mid, nickname in rows:
        out.setdefault(mid, []).append(nickname or '用户')
    return out


def _serialize(moment, me, medias, liked_ids, like_counts, comment_counts, comments, likers):
    u = User.query.get(moment.user_id)
    return {
        "moment_id": moment.id,
        "user_id": moment.user_id,
        "user_nickname": u.nickname if u else f"用户 {moment.user_id}",
        "user_avatar": u.avatar if u else None,
        "content": moment.content,
        "visibility": moment.visibility,
        "created_at": moment.created_at.isoformat() if moment.created_at else None,
        "medias": medias.get(moment.id, []),
        "like_count": like_counts.get(moment.id, 0),
        "comment_count": comment_counts.get(moment.id, 0),
        "liked": moment.id in liked_ids,
        "is_self": moment.user_id == me,
        "comments": comments.get(moment.id, []),
        "likers": likers.get(moment.id, []),
    }


def _counts(moment_ids):
    like_counts, comment_counts, liked_ids = {}, {}, set()
    if not moment_ids:
        return like_counts, comment_counts, liked_ids
    from sqlalchemy import func
    for mid, cnt in db.session.query(MomentLike.moment_id, func.count(MomentLike.id)).filter(
            MomentLike.moment_id.in_(moment_ids)).group_by(MomentLike.moment_id).all():
        like_counts[mid] = cnt
    for mid, cnt in db.session.query(MomentComment.moment_id, func.count(MomentComment.id)).filter(
            MomentComment.moment_id.in_(moment_ids)).group_by(MomentComment.moment_id).all():
        comment_counts[mid] = cnt
    for lk in MomentLike.query.filter(MomentLike.moment_id.in_(moment_ids), MomentLike.user_id == g.user.id).all():
        liked_ids.add(lk.moment_id)
    return like_counts, comment_counts, liked_ids


# --------------------------------------------------------------------------
@bp.route('/publish', methods=['POST'])
@login_required
def publish_moment():
    """发布动态（multipart）：content, visibility(1/2/3), files[]（图片/视频，可选）"""
    try:
        content = (request.form.get('content') or '').strip()
        try:
            visibility = int(request.form.get('visibility', 1))
        except (TypeError, ValueError):
            visibility = 1
        if visibility not in (1, 2, 3):
            visibility = 1
        files = request.files.getlist('files')
        if not content and not files:
            return resp_json(PARAMS_ERROR, "动态内容不能为空", data=None)
        if is_user_muted(g.user):
            return resp_json(PARAMS_ERROR, "你已被禁言，暂时无法发布动态", data=None)
        ok, smsg = guard_sensitive(content, g.user.id, 'moment')
        if not ok:
            return resp_json(PARAMS_ERROR, smsg, data=None)
        moment = Moment(user_id=g.user.id, content=content or '', visibility=visibility, status=1)
        db.session.add(moment)
        db.session.flush()
        order = 0
        for f in files:
            if not f or not f.filename:
                continue
            result = save_upload_file(f, scope=('moment', g.user.id))
            if result is None:
                continue
            url, file_type, _ = result
            # 朋友圈仅收图片(3)/视频(2)，其他类型丢弃并清理已落盘文件
            if file_type == 3:
                media_type = 1
            elif file_type == 2:
                media_type = 2
            else:
                delete_old_file(url)
                continue
            db.session.add(MomentMedia(moment_id=moment.id, url=url, media_type=media_type, sort_order=order))
            order += 1
        db.session.commit()
        return resp_json(OK, "发布成功", data={"moment_id": moment.id})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/feed', methods=['GET'])
@login_required
def feed():
    """好友动态流：自己的全部 + 好友的公开/好友可见动态，分页"""
    try:
        me = g.user.id
        fset = _friend_ids(me)
        last_id = request.args.get('last_id', type=int)
        per_page = request.args.get('per_page', 20, type=int)
        per_page = min(max(per_page, 1), 50)
        q = Moment.query.filter(Moment.status == 1)
        if fset:
            q = q.filter(db.or_(Moment.user_id == me,
                                (Moment.user_id.in_(fset)) & (Moment.visibility.in_([1, 2]))))
        else:
            q = q.filter(Moment.user_id == me)
        if last_id:
            q = q.filter(Moment.id < last_id)
        # 拉黑双向：屏蔽我拉黑的人 & 拉黑我的人的动态
        excl = _blocked_pair_ids(me)
        if excl:
            q = q.filter(Moment.user_id.notin_(excl))
        moments = q.order_by(Moment.id.desc()).limit(per_page).all()
        ids = [m.id for m in moments]
        medias = _media_map(ids)
        like_counts, comment_counts, liked_ids = _counts(ids)
        comments_map = _comment_map(ids)
        likers_map = _liker_map(ids)
        data = [_serialize(m, me, medias, liked_ids, like_counts, comment_counts, comments_map, likers_map) for m in moments]
        return resp_json(OK, "获取成功", data={"moments": data, "has_more": len(moments) == per_page})
    except Exception as e:
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/user/<int:uid>', methods=['GET'])
@login_required
def user_moments(uid):
    """查看某用户的动态：本人看全部；好友看公开/好友可见；陌生人仅看公开"""
    try:
        me = g.user.id
        if uid == me:
            allowed = [1, 2, 3]
        else:
            owner = User.query.get(uid)
            if not owner:
                return resp_json(OK, "获取成功", data={"moments": [], "total": 0})
            # 资料卡全局闸门（profile_visibility）：3=仅自己→外人一律不可见；2=仅好友→非好友不可见
            pv = owner.profile_visibility or 1
            if pv == 3:
                return resp_json(OK, "获取成功", data={"moments": [], "total": 0})
            is_friend = uid in _friend_ids(me)
            if pv == 2 and not is_friend:
                return resp_json(OK, "获取成功", data={"moments": [], "total": 0})
            # 通过全局闸门后，再按每条动态各自的可见性过滤：好友看公开/好友可见，陌生人仅公开
            allowed = [1, 2] if is_friend else [1]
        # 拉黑双向：看不到对方动态，也看不到自己的被屏蔽（本人 uid==me 不受限）
        if uid != me and uid in _blocked_pair_ids(me):
            return resp_json(OK, "获取成功", data={"moments": [], "total": 0})
        moments = Moment.query.filter(Moment.user_id == uid, Moment.status == 1,
                                      Moment.visibility.in_(allowed)) \
            .order_by(Moment.id.desc()).limit(50).all()
        ids = [m.id for m in moments]
        medias = _media_map(ids)
        like_counts, comment_counts, liked_ids = _counts(ids)
        comments_map = _comment_map(ids)
        likers_map = _liker_map(ids)
        data = [_serialize(m, me, medias, liked_ids, like_counts, comment_counts, comments_map, likers_map) for m in moments]
        return resp_json(OK, "获取成功", data={"moments": data, "total": len(data)})
    except Exception as e:
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/<int:moment_id>', methods=['GET'])
@login_required
def moment_detail(moment_id):
    """按 id 取单条动态（供通知跳转定位）：遵守可见性 1=公开 2=好友可见 3=仅自己"""
    try:
        me = g.user.id
        moment = Moment.query.filter_by(id=moment_id, status=1).first()
        if not moment:
            return resp_json(PARAMS_ERROR, "动态不存在或已删除", data=None)
        if moment.user_id != me:
            if moment.visibility == 3:
                return resp_json(PARAMS_ERROR, "该动态不可见", data=None)
            if moment.visibility == 2 and moment.user_id not in _friend_ids(me):
                return resp_json(PARAMS_ERROR, "该动态不可见", data=None)
        ids = [moment.id]
        medias = _media_map(ids)
        like_counts, comment_counts, liked_ids = _counts(ids)
        comments_map = _comment_map(ids)
        likers_map = _liker_map(ids)
        data = _serialize(moment, me, medias, liked_ids, like_counts, comment_counts, comments_map, likers_map)
        return resp_json(OK, "获取成功", data=data)
    except Exception as e:
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/like', methods=['POST'])
@login_required
def toggle_like():
    """点赞/取消点赞：{moment_id}"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "请求参数为空", data=None)
        moment_id = data.get('moment_id')
        moment = Moment.query.get(moment_id)
        if not moment or moment.status != 1:
            return resp_json(PARAMS_ERROR, "动态不存在", data=None)
        exist = MomentLike.query.filter_by(moment_id=moment_id, user_id=g.user.id).first()
        if exist:
            db.session.delete(exist)
            db.session.commit()
            liked = False
        else:
            db.session.add(MomentLike(moment_id=moment_id, user_id=g.user.id))
            # 给作者发通知（非自己）
            if moment.user_id != g.user.id:
                create_like_notification(moment, g.user.nickname)
            db.session.commit()
            liked = True
        count = MomentLike.query.filter_by(moment_id=moment_id).count()
        likers = [n for (n,) in db.session.query(User.nickname).join(
            MomentLike, MomentLike.user_id == User.id
        ).filter(MomentLike.moment_id == moment_id).order_by(MomentLike.id).all()]
        return resp_json(OK, "操作成功", data={"liked": liked, "like_count": count, "likers": likers})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


def create_like_notification(moment, nickname):
    from utils import create_notification
    create_notification(moment.user_id, 'like', title='收到点赞',
                        content=f"{nickname} 赞了你的动态",
                        related_id=moment.id, related_type='moment')
    socketio.emit('notification', {
        'type': 'like', 'title': '收到点赞',
        'content': f"{nickname} 赞了你的动态",
        'related_id': moment.id, 'related_type': 'moment'
    }, room=str(moment.user_id))


@bp.route('/comment', methods=['POST'])
@login_required
def add_comment():
    """评论动态：{moment_id, content, reply_to?}"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "请求参数为空", data=None)
        moment_id = data.get('moment_id')
        content = (data.get('content') or '').strip()
        reply_to = data.get('reply_to')
        if not content:
            return resp_json(PARAMS_ERROR, "评论内容不能为空", data=None)
        moment = Moment.query.get(moment_id)
        if not moment or moment.status != 1:
            return resp_json(PARAMS_ERROR, "动态不存在", data=None)
        content = content[:500]
        if is_user_muted(g.user):
            return resp_json(PARAMS_ERROR, "你已被禁言，暂时无法评论", data=None)
        ok, smsg = guard_sensitive(content, g.user.id, 'moment')
        if not ok:
            return resp_json(PARAMS_ERROR, smsg, data=None)
        comment = MomentComment(moment_id=moment_id, user_id=g.user.id, content=content,
                                reply_to=int(reply_to) if reply_to else None)
        db.session.add(comment)
        db.session.flush()
        from utils import create_notification
        # 通知动态作者（非自己）
        if moment.user_id != g.user.id:
            create_notification(moment.user_id, 'reply', title='收到评论',
                                content=f"{g.user.nickname} 评论了你的动态：{content[:30]}",
                                related_id=moment.id, related_type='moment')
            socketio.emit('notification', {
                'type': 'reply', 'title': '收到评论',
                'content': f"{g.user.nickname} 评论了你的动态",
                'related_id': moment.id, 'related_type': 'moment'
            }, room=str(moment.user_id))
        # 回复场景：额外通知被回复者（非自己、且与动态作者不同，避免重复）
        reply_nick = None
        if comment.reply_to:
            tgt = MomentComment.query.get(comment.reply_to)
            if tgt:
                tu = User.query.get(tgt.user_id)
                reply_nick = tu.nickname if tu else None
                if tgt.user_id != g.user.id and tgt.user_id != moment.user_id:
                    create_notification(tgt.user_id, 'reply', title='收到回复',
                                        content=f"{g.user.nickname} 回复了你：{content[:30]}",
                                        related_id=moment.id, related_type='moment')
                    socketio.emit('notification', {
                        'type': 'reply', 'title': '收到回复',
                        'content': f"{g.user.nickname} 回复了你的评论",
                        'related_id': moment.id, 'related_type': 'moment'
                    }, room=str(tgt.user_id))
        db.session.commit()
        return resp_json(OK, "评论成功", data={
            "comment_id": comment.id,
            "moment_id": moment_id,
            "user_id": g.user.id,
            "user_nickname": g.user.nickname,
            "user_avatar": g.user.avatar,
            "content": content,
            "reply_to": comment.reply_to,
            "reply_to_nickname": reply_nick,
            "created_at": datetime.datetime.utcnow().isoformat(),
        })
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/<int:moment_id>/comments', methods=['GET'])
@login_required
def list_comments(moment_id):
    """动态评论列表"""
    try:
        comments = MomentComment.query.filter_by(moment_id=moment_id).order_by(MomentComment.id).all()
        uids = {c.user_id for c in comments}
        users = {u.id: u for u in User.query.filter(User.id.in_(uids)).all()} if uids else {}
        by_id = {c.id: c for c in comments}
        data = []
        for c in comments:
            reply_nick = None
            if c.reply_to:
                tgt = by_id.get(c.reply_to)
                if tgt:
                    tu = users.get(tgt.user_id)
                    reply_nick = tu.nickname if tu else None
            data.append({
                "comment_id": c.id,
                "user_id": c.user_id,
                "user_nickname": users[c.user_id].nickname if c.user_id in users else f"用户 {c.user_id}",
                "user_avatar": users[c.user_id].avatar if c.user_id in users else None,
                "content": c.content,
                "reply_to": c.reply_to,
                "reply_to_nickname": reply_nick,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            })
        return resp_json(OK, "获取成功", data={"comments": data, "total": len(data)})
    except Exception as e:
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/comment/delete', methods=['POST'])
@login_required
def delete_comment():
    """删除自己发的动态评论（物理删）：{comment_id}"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "请求参数为空", data=None)
        comment_id = data.get('comment_id')
        comment = MomentComment.query.get(comment_id)
        if not comment:
            return resp_json(PARAMS_ERROR, "评论不存在", data=None)
        if comment.user_id != g.user.id:
            return resp_json(PARAMS_ERROR, "只能删除自己的评论", data=None)
        db.session.delete(comment)
        db.session.commit()
        return resp_json(OK, "已删除评论", data={"comment_id": comment_id})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)


@bp.route('/delete', methods=['POST'])
@login_required
def delete_moment():
    """删除自己的动态（软删）：{moment_id}"""
    try:
        data = request.get_json(silent=True)
        if not data:
            return resp_json(PARAMS_ERROR, "请求参数为空", data=None)
        moment_id = data.get('moment_id')
        moment = Moment.query.get(moment_id)
        if not moment:
            return resp_json(PARAMS_ERROR, "动态不存在", data=None)
        if moment.user_id != g.user.id:
            return resp_json(PARAMS_ERROR, "只能删除自己的动态", data=None)
        moment.status = 0
        db.session.commit()
        return resp_json(OK, "已删除动态", data={"moment_id": moment_id})
    except Exception as e:
        db.session.rollback()
        return resp_json(SERVER_ERROR, f"服务器内部错误：{str(e)}", data=None)
