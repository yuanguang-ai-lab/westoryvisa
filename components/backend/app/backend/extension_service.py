"""Paid website-to-extension tasks. Server-owned leases and revocable tokens."""
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .extension_plan import build_plan

ORIGIN = "https://westoryvisa.com"
SCHEMA = """
CREATE TABLE IF NOT EXISTS extension_jobs (
 id TEXT PRIMARY KEY,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
 case_id TEXT NOT NULL REFERENCES ds160_cases(id) ON DELETE CASCADE,
 session_hash TEXT NOT NULL,
 token_hash TEXT NOT NULL,
 extension_id TEXT NOT NULL,
 plan_json TEXT NOT NULL,
 state TEXT NOT NULL,
 status_json TEXT NOT NULL,
 sequence BIGINT NOT NULL DEFAULT -1,
 created_at TEXT NOT NULL,
 expires_at TEXT NOT NULL,
 lease_until TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS extension_jobs_owner ON extension_jobs(organization_id, user_id, lease_until);
CREATE TABLE IF NOT EXISTS extension_job_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 job_id TEXT NOT NULL REFERENCES extension_jobs(id) ON DELETE CASCADE,
 sequence BIGINT NOT NULL,
 status_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(job_id, sequence)
);
"""

class AccessError(Exception):
    def __init__(self, message, status=403, code="extension_access_denied"):
        super().__init__(message)
        self.status, self.code = status, code

def stamp():
    return datetime.now(timezone.utc).isoformat()

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def lock_org(conn, org):
    if getattr(conn, "dialect", "sqlite") == "postgresql":
        number = int.from_bytes(hashlib.sha256(("extension:"+org).encode()).digest()[:8], "big", signed=True)
        conn.execute("SELECT pg_advisory_xact_lock(?)", (number,))
    else:
        conn.execute("BEGIN IMMEDIATE")

def check_member(conn, user_id, org, session_hash):
    now = stamp()
    session = conn.execute(
        "SELECT u.id FROM users u JOIN auth_sessions s ON s.user_id=u.id "
        "WHERE u.id=? AND u.organization_id=? AND s.token_hash=? AND s.expires_at>?",
        (user_id, org, session_hash, now)).fetchone()
    if not session:
        raise AccessError("登录已失效，请重新登录网站并准备任务", 401, "session_expired")
    member = conn.execute(
        "SELECT id FROM billing_subscriptions WHERE organization_id=? AND status='active' AND current_period_end>?",
        (org, now)).fetchone()
    if not member:
        raise AccessError("插件填写需要有效会员，请先开通或续费", 402, "membership_required")

def scrub_expired(conn):
    conn.execute("UPDATE extension_jobs SET state='expired', plan_json='[]', token_hash='' "
                 "WHERE state IN ('prepared','running','blocked') AND (expires_at<=? OR lease_until<=?)",
                 (stamp(), stamp()))

def create_job(app, user, cookie, case_id, extension_id):
    if not re.fullmatch(r"[a-p]{32}", extension_id or ""):
        raise AccessError("请安装正式插件并刷新网站", 400)
    session_hash = app.session_token_hash(app.token_from_cookie(cookie))
    # Reject unpaid requests before building a plan or accessing translations.
    with app.connect() as conn:
        check_member(conn, user['id'], user['organizationId'], session_hash)
    if user.get('serviceCountry', 'CN') != 'CN':
        raise AccessError('当前插件通道仅开放中国版 DS-160', 409)
    payload = app.get_case_payload(case_id, user)
    if payload.get('serviceCountry', payload.get('countryCode', 'CN')) not in ('CN', None, ''):
        raise AccessError("当前插件通道仅开放中国版 DS-160", 409)
    pages = build_plan(payload)
    job_id, token = 'codex-agent-' + secrets.token_hex(12), secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with app.connect() as conn:
        lock_org(conn, user['organizationId'])
        check_member(conn, user['id'], user['organizationId'], session_hash)
        scrub_expired(conn)
        active = conn.execute("SELECT id, user_id, case_id FROM extension_jobs WHERE organization_id=? "
                              "AND state IN ('prepared','running','blocked')", (user['organizationId'],)).fetchall()
        if any(r['user_id']==user['id'] or r['case_id']==case_id for r in active):
            raise AccessError("已有插件任务占用此账号或档案，请先停止原任务；断线任务两分钟后释放", 409, "concurrency_limit")
        if len(active) >= max(1, int(os.environ.get('DOCFLOW_EXTENSION_ORG_CONCURRENCY', '3'))):
            raise AccessError("机构同时填写数量已达上限", 409, "concurrency_limit")
        expires = (now+timedelta(minutes=60)).isoformat()
        status = dict(jobId=job_id, state='prepared', totalFields=sum(len(p['actions']) for p in pages),
                      completedFields=0, pageLabel='', message='任务已连接当前登录账号，等待开始', expiresAt=expires)
        conn.execute("INSERT INTO extension_jobs (id,user_id,organization_id,case_id,session_hash,token_hash,extension_id,"
                     "plan_json,state,status_json,created_at,expires_at,lease_until) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (job_id,user['id'],user['organizationId'],case_id,session_hash,digest(token),extension_id,
                      json.dumps(pages,ensure_ascii=False),'prepared',json.dumps(status,ensure_ascii=False),now.isoformat(),expires,
                      (now+timedelta(seconds=120)).isoformat()))
    return dict(status, taskUrl=f'{ORIGIN}/api/extension/jobs/{job_id}', accessToken=token)

def authorize(conn, job_id, token, extension_id):
    row = conn.execute('SELECT * FROM extension_jobs WHERE id=?', (job_id,)).fetchone()
    if not row or not row['token_hash'] or not hmac.compare_digest(row['token_hash'], digest(token)):
        raise AccessError('任务授权无效或已结束', 401)
    if row['extension_id'] != extension_id:
        raise AccessError('插件与任务授权不匹配')
    if row['expires_at'] <= stamp() or row['lease_until'] <= stamp():
        raise AccessError('任务连接已过期，请从网站重新准备', 401, 'lease_expired')
    check_member(conn, row['user_id'], row['organization_id'], row['session_hash'])
    return row

def read_plan(app, job_id, token, extension_id):
    with app.connect() as conn:
        row = authorize(conn, job_id, token, extension_id)
        pages = json.loads(row['plan_json'])
        return dict(version=2, executor='chrome-extension-v1', workflowType='ds160', jobId=job_id, page='workflow',
                    targetUrl='https://ceac.state.gov/GenNIV/Default.aspx',pages=pages,autoNext=True,
                    safety={'allowedDomain':'ceac.state.gov'},expiresAt=row['expires_at'],
                    statusUrl=f'{ORIGIN}/api/extension/jobs/{job_id}/status')

def progress(app, job_id, token, extension_id, payload):
    # Serialize with stop/create so a delayed status cannot resurrect a revoked task.
    with app.connect() as conn:
        owner = conn.execute('SELECT organization_id FROM extension_jobs WHERE id=?',(job_id,)).fetchone()
        if not owner: raise AccessError('任务不存在',404)
        lock_org(conn, owner['organization_id'])
        row = authorize(conn, job_id, token, extension_id)
        seq = int(payload.get('eventSequence',0))
        if seq <= row['sequence']: return {'ok':True}
        pages = json.loads(row['plan_json']); ids={a['id'] for p in pages for a in p['actions']}
        state = payload.get('state','running')
        if state not in ('waiting_for_entry','running','blocked','failed','review_required','completed','revoked'):
            raise AccessError('无效任务状态',400)
        completed=max(0,min(len(ids),int(payload.get('completedFields',0))))
        current_route=payload.get('currentRoute') or {}
        at_photo = current_route.get('node','').lower()=='uploadphoto'
        finished = state in ('revoked','completed','review_required') or (at_photo and completed==len(ids))
        status=dict(jobId=job_id,state='completed' if at_photo and finished else state,
                    completedFields=completed,totalFields=len(ids),
                    pageLabel=str(payload.get('pageLabel',''))[:100],
                    statusCode='photo_reached' if at_photo and finished else str(payload.get('statusCode',''))[:64],
                    message='已完成至照片页' if at_photo and finished else str(payload.get('reason',''))[:240],
                    lastActionId=payload.get('lastActionId','') if payload.get('lastActionId','') in ids else '',
                    updatedAt=stamp(),expiresAt=row['expires_at'])
        encoded=json.dumps(status,ensure_ascii=False)
        conn.execute('UPDATE extension_jobs SET state=?,status_json=?,sequence=?,lease_until=?,plan_json=?,token_hash=? WHERE id=?',
                     ('completed' if finished else 'running',encoded,seq,
                      (datetime.now(timezone.utc)+timedelta(seconds=120)).isoformat(),
                      '[]' if finished else row['plan_json'], '' if finished else row['token_hash'],job_id))
        conn.execute('INSERT INTO extension_job_events(job_id,sequence,status_json,created_at) VALUES (?,?,?,?)',
                     (job_id,seq,encoded,stamp()))
    return {'ok':True,'finished':finished}

def owner_status(app, user, job_id, stop=False):
    with app.connect() as conn:
        lock_org(conn,user['organizationId'])
        row=conn.execute('SELECT * FROM extension_jobs WHERE id=? AND organization_id=? AND user_id=?',
                         (job_id,user['organizationId'],user['id'])).fetchone()
        if not row: raise AccessError('任务不存在',404)
        status=json.loads(row['status_json'])
        if stop or (row['lease_until']<=stamp() and row['state'] not in ('completed','revoked')):
            status.update(state='revoked' if stop else 'expired',message='任务已停止' if stop else '连接过期，请重新准备')
            conn.execute("UPDATE extension_jobs SET state=?,token_hash='',plan_json='[]',status_json=? WHERE id=?",
                         (status['state'],json.dumps(status,ensure_ascii=False),job_id))
        return status

def dispatch(handler, app):
    path=urlparse(handler.path).path
    if not path.startswith('/api/extension/'): return False
    origin=handler.headers.get('Origin','')
    ext_match=re.fullmatch(r'chrome-extension://([a-p]{32})',origin)
    is_token_route=bool(re.fullmatch(r'/api/extension/jobs/codex-agent-[0-9a-f]{24}(?:/status)?',path))
    cors = origin == ORIGIN or (ext_match and is_token_route)
    def respond(data,status=200):
        raw=json.dumps(data,ensure_ascii=False).encode()
        handler.send_response(status)
        handler.send_header('Content-Type','application/json; charset=utf-8')
        handler.send_header('Content-Length',str(len(raw)))
        handler.send_header('Cache-Control','no-store')
        if cors:
            handler.send_header('Access-Control-Allow-Origin',origin)
            handler.send_header('Vary','Origin')
            if origin==ORIGIN: handler.send_header('Access-Control-Allow-Credentials','true')
            handler.send_header('Access-Control-Allow-Headers','Authorization, Content-Type, X-DocFlow-Extension-Id, X-DocFlow-Country')
            handler.send_header('Access-Control-Allow-Methods','GET, POST, DELETE, OPTIONS')
        handler.end_headers();handler.wfile.write(raw)
    try:
        if origin and not cors: raise AccessError('Cross-origin request denied')
        if handler.command=='OPTIONS': respond({'ok':True});return True
        if path=='/api/extension/health':
            respond({'ok':True,'version':'1.0.5','membershipRequired':True,'perUserConcurrency':1});return True
        token=handler.headers.get('Authorization','').removeprefix('Bearer ')
        match=re.fullmatch(r'/api/extension/jobs/(codex-agent-[0-9a-f]{24})(/status)?',path)
        if token and match:
            extension_id=handler.headers.get('X-DocFlow-Extension-Id','')
            if ext_match and extension_id!=ext_match[1]: raise AccessError('插件来源不匹配')
            if handler.command=='GET' and not match[2]: data=read_plan(app,match[1],token,extension_id)
            elif handler.command=='POST' and match[2]:
                if int(handler.headers.get('Content-Length','0'))>65536: raise AccessError('请求过大',413)
                data=progress(app,match[1],token,extension_id,handler.read_json())
            else: raise AccessError('不支持的请求',405)
            respond(data);return True
        if origin!=ORIGIN and handler.command in ('POST','DELETE'): raise AccessError('请从正式网站操作')
        user=handler.current_user()
        if not user: raise AccessError('请先登录网站',401,'login_required')
        if path=='/api/extension/jobs' and handler.command=='POST':
            if int(handler.headers.get('Content-Length','0'))>4096: raise AccessError('请求过大',413)
            body=handler.read_json()
            data=create_job(app,user,handler.headers.get('Cookie',''),str(body.get('caseId','')),body.get('extensionId',''))
            respond(data,201)
        elif match and not match[2] and handler.command in ('GET','DELETE'):
            respond(owner_status(app,user,match[1],handler.command=='DELETE'))
        else: raise AccessError('Not found',404)
    except AccessError as error: respond({'error':str(error),'code':error.code},error.status)
    except (ValueError,TypeError,KeyError) as error: respond({'error':str(error)[:240]},400)
    except PermissionError: respond({'error':'没有该档案的访问权限'},403)
    return True
