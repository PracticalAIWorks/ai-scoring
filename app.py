"""AI 创新应用大赛。开发：python app.py；生产：waitress-serve app:application。"""
import base64
import hashlib
import hmac
import io
import json
import math
import mimetypes
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent
# Local preview and Docker Compose share the same ignored password configuration.
if not os.environ.get('ADMIN_PASSWORD') and (ROOT / '.env').is_file():
    for config_line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines():
        config_key, separator, config_value = config_line.strip().partition('=')
        if separator and config_key == 'ADMIN_PASSWORD':
            os.environ['ADMIN_PASSWORD'] = config_value.strip().strip('\"\'')
DATA = Path(os.environ.get('DATA_DIR', ROOT / 'data')).resolve()
DATA.mkdir(parents=True, exist_ok=True)
(DATA / 'media').mkdir(exist_ok=True)
DB = DATA / 'scores.sqlite3'
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD') or secrets.token_urlsafe(12)
COOKIE_SECURE = os.environ.get('COOKIE_SECURE', '0') == '1'
RUBRICS = json.loads((ROOT / 'rubrics.json').read_text(encoding='utf-8'))
CATEGORIES = {c['id']: c for c in RUBRICS}


@contextmanager
def connect():
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        with db:
            yield db
    finally:
        db.close()


def init_db():
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS judges(id TEXT PRIMARY KEY, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, category TEXT NOT NULL, number INTEGER NOT NULL,
          title TEXT NOT NULL, person TEXT NOT NULL, department TEXT NOT NULL, intro TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS scores(judge_id TEXT NOT NULL REFERENCES judges(id), project_id TEXT NOT NULL REFERENCES projects(id),
          dimension INTEGER NOT NULL, value REAL NOT NULL, updated TEXT NOT NULL,
          PRIMARY KEY(judge_id,project_id,dimension));
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS write_versions(client TEXT NOT NULL, judge_id TEXT NOT NULL, project_id TEXT NOT NULL,
          dimension INTEGER NOT NULL, version INTEGER NOT NULL, PRIMARY KEY(client,judge_id,project_id,dimension));
        ''')
        if not db.execute("SELECT 1 FROM settings WHERE key='initialized'").fetchone():
            db.executemany('INSERT INTO settings VALUES (?,?)', [('initialized','1'),('title','AI 创新应用大赛'),('subtitle','让创新被看见，让价值被发现'),('poster','/static/competition-poster.png')])
            for i in range(1,4):
                db.execute('INSERT INTO judges(id,name) VALUES (?,?)', (f'demo-judge-{i}',f'演示评委{i}'))
            samples = [
                ('prompt','用 AI 写好每一份工作简报','行政部','通过结构化提示词，将会议记录整理为重点清晰的工作简报，减少重复整理时间。'),
                ('prompt','让客户沟通更有温度','客户服务部','面向常见服务场景设计可复用的沟通提示词，兼顾准确性、表达质量与响应效率。'),
                ('solution','从数据到决策的智能助手','运营部','整合业务指标与知识资料，通过 AI 分析辅助识别异常，形成可执行的运营建议。'),
                ('solution','业务流程智能优化方案','业务部','围绕重复性业务环节，设计人机协同流程，明确数据输入、处理步骤与验收指标。'),
                ('agent','企业知识库问答智能体','信息技术部','基于企业知识库回答常见问题，提供资料依据，并对缺失信息给出明确提示。'),
                ('agent','让日常办公更高效','综合管理部','通过智能体串联信息查询、材料整理与任务提醒，展示从输入到输出的完整工作流。')]
            for i,(category,title,dept,intro) in enumerate(samples,1):
                db.execute('INSERT INTO projects(id,category,number,title,person,department,intro) VALUES (?,?,?,?,?,?,?)',
                    (f'demo-project-{i}',category,(i-1)%2+1,title,f'演示参赛人{i}',dept,'【演示项目，可在管理后台替换】'+intro))


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class ApiError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def json_body(env):
    try:
        length = int(env.get('CONTENT_LENGTH') or 0)
        if length > 9 * 1024 * 1024:
            raise ApiError('文件过大，请使用 6MB 以内的图片',413)
        value = json.loads(env['wsgi.input'].read(length) or b'{}')
        if not isinstance(value,dict):
            raise ValueError()
        return value
    except (ValueError,UnicodeDecodeError):
        raise ApiError('请求格式错误')


def required_text(body,key,maxlen=200):
    value = body.get(key)
    if not isinstance(value,str) or not value.strip() or len(value.strip()) > maxlen:
        raise ApiError(f'{key} 不能为空，且不能超过 {maxlen} 字')
    return value.strip()


def admin_session(env,db):
    cookie = SimpleCookie()
    try:
        cookie.load(env.get('HTTP_COOKIE',''))
        token = cookie['admin_session'].value if 'admin_session' in cookie else ''
    except Exception:
        token = ''
    hashed = hashlib.sha256(token.encode()).hexdigest()
    if not db.execute('SELECT 1 FROM sessions WHERE token=? AND expires>?',(hashed,time.time())).fetchone():
        raise ApiError('请先打开管理后台并验证管理密码',401)


def snapshot(db):
    return {
        'settings':dict(db.execute('SELECT key,value FROM settings WHERE key != "initialized"').fetchall()),
        'categories':RUBRICS,
        'judges':[dict(r) for r in db.execute('SELECT * FROM judges ORDER BY rowid')],
        'projects':[dict(r) for r in db.execute('SELECT * FROM projects ORDER BY category,number,rowid')],
    }


def all_records(db):
    data = snapshot(db)
    rows = db.execute('SELECT * FROM scores').fetchall()
    scores = {}
    for r in rows:
        scores.setdefault((r['judge_id'],r['project_id']),{})[r['dimension']] = r['value']
    records = []
    for p in data['projects']:
        dims = CATEGORIES[p['category']]['dimensions']
        for j in data['judges']:
            vals = scores.get((j['id'],p['id']),{})
            complete = len(vals) == len(dims)
            records.append({'project_id':p['id'],'judge_id':j['id'],'values':{str(k):v for k,v in vals.items()},
              'complete':complete,'total':round(sum(vals.values()),2) if complete else None,
              'subtotal':round(sum(vals.values()),2)})
    return data,records


def excel_bytes(db):
    from excel_export import build_report
    data, records = all_records(db)
    return build_report(data, records, RUBRICS)


LOGIN_FAILURES = {}


def application(env,start_response):
    path=env.get('PATH_INFO','/')
    method=env['REQUEST_METHOD']
    headers=[]
    status=200
    mime='application/json; charset=utf-8'
    try:
        # Browser writes must originate from this application. No wildcard CORS.
        if method not in ('GET','HEAD') and env.get('HTTP_ORIGIN'):
            if urlparse(env['HTTP_ORIGIN']).netloc != env.get('HTTP_HOST'):
                raise ApiError('不允许跨站修改',403)
        with connect() as db:
            if path=='/api/bootstrap' and method=='GET':
                data=snapshot(db)
                data['judges']=[j for j in data['judges'] if j['active']]
                data['projects']=[p for p in data['projects'] if p['active']]
            elif path=='/api/scores' and method=='GET':
                judge=parse_qs(env.get('QUERY_STRING','')).get('judge',[''])[0]
                if not db.execute('SELECT 1 FROM judges WHERE id=? AND active=1',(judge,)).fetchone(): raise ApiError('请选择有效评委')
                data={'scores':[dict(r) for r in db.execute('SELECT project_id,dimension,value,updated FROM scores WHERE judge_id=?',(judge,))]}
            elif path=='/api/score' and method=='PUT':
                b=json_body(env)
                judge=required_text(b,'judge_id'); project=required_text(b,'project_id')
                if not db.execute('SELECT 1 FROM judges WHERE id=? AND active=1',(judge,)).fetchone(): raise ApiError('该评委已停用，请返回首页重新选择')
                p=db.execute('SELECT * FROM projects WHERE id=? AND active=1',(project,)).fetchone()
                if not p: raise ApiError('项目不存在或已停用')
                k=b.get('dimension'); v=b.get('value')
                dims=CATEGORIES[p['category']]['dimensions']
                if type(k) is not int or k<0 or k>=len(dims): raise ApiError('无效评分项')
                if v is not None and (type(v) not in (int,float) or not math.isfinite(v) or v<0 or v>dims[k]['max'] or abs(v*100-round(v*100))>1e-7):
                    raise ApiError(f"分数应为 0～{dims[k]['max']}，最多两位小数")
                stamp=now()
                apply_write=True
                if 'client' in b:
                    client=required_text(b,'client',100); version=b.get('version')
                    if type(version) is not int or version<0 or version>9007199254740991: raise ApiError('同步版本无效')
                    result=db.execute('INSERT INTO write_versions VALUES (?,?,?,?,?) ON CONFLICT(client,judge_id,project_id,dimension) DO UPDATE SET version=excluded.version WHERE excluded.version>write_versions.version',(client,judge,project,k,version))
                    apply_write=result.rowcount>0
                if not apply_write:
                    pass
                elif v is None:
                    db.execute('DELETE FROM scores WHERE judge_id=? AND project_id=? AND dimension=?',(judge,project,k))
                else:
                    db.execute('INSERT INTO scores VALUES (?,?,?,?,?) ON CONFLICT(judge_id,project_id,dimension) DO UPDATE SET value=excluded.value,updated=excluded.updated',(judge,project,k,v,stamp))
                data={'saved':True,'updated':stamp,'stale':not apply_write}
            elif path=='/api/admin/login' and method=='POST':
                b=json_body(env); ip=env.get('REMOTE_ADDR','')
                hits=[t for t in LOGIN_FAILURES.get(ip,[]) if t>time.time()-300]
                if len(hits)>=10: raise ApiError('尝试次数过多，请五分钟后重试',429)
                if not hmac.compare_digest(str(b.get('password','')).encode(),ADMIN_PASSWORD.encode()):
                    LOGIN_FAILURES[ip]=hits+[time.time()]
                    raise ApiError('管理密码不正确',401)
                LOGIN_FAILURES.pop(ip,None)
                token=secrets.token_urlsafe(32)
                db.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
                db.execute('INSERT INTO sessions VALUES (?,?)',(hashlib.sha256(token.encode()).hexdigest(),time.time()+43200))
                headers.append(('Set-Cookie',f'admin_session={token}; HttpOnly; SameSite=Strict; Path=/api/admin; Max-Age=43200'+('; Secure' if COOKIE_SECURE else '')))
                data={'ok':True}
            elif path.startswith('/api/admin/'):
                admin_session(env,db)
                if path=='/api/admin/state' and method=='GET':
                    data,records=all_records(db); data['records']=records
                elif path=='/api/admin/logout' and method=='POST':
                    c=SimpleCookie(); c.load(env.get('HTTP_COOKIE',''))
                    db.execute('DELETE FROM sessions WHERE token=?',(hashlib.sha256(c['admin_session'].value.encode()).hexdigest(),))
                    headers.append(('Set-Cookie','admin_session=; HttpOnly; SameSite=Strict; Path=/api/admin; Max-Age=0'))
                    data={'ok':True}
                elif path=='/api/admin/judges' and method=='POST':
                    b=json_body(env); jid=b.get('id') or uuid.uuid4().hex
                    name=required_text(b,'name',50); active=1 if b.get('active',True) else 0
                    db.execute('INSERT INTO judges VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,active=excluded.active',(jid,name,active))
                    data={'id':jid}
                elif path=='/api/admin/projects' and method=='POST':
                    b=json_body(env); pid=b.get('id') or uuid.uuid4().hex
                    old=db.execute('SELECT category,intro FROM projects WHERE id=?',(pid,)).fetchone()
                    cat=b.get('category'); num=b.get('number')
                    if cat not in CATEGORIES or type(num) is not int or num<1 or num>99999: raise ApiError('分类或序号无效')
                    if old and old['category']!=cat and db.execute('SELECT 1 FROM scores WHERE project_id=?',(pid,)).fetchone(): raise ApiError('该项目已有评分，不能更换评分分类，请新增项目')
                    intro=b.get('intro',old['intro'] if old else '')
                    if not isinstance(intro,str) or len(intro)>10000: raise ApiError('项目介绍不能超过 10000 字')
                    args=(pid,cat,num,required_text(b,'title'),required_text(b,'person',100),required_text(b,'department',100),intro,1 if b.get('active',True) else 0)
                    db.execute('INSERT INTO projects VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET category=excluded.category,number=excluded.number,title=excluded.title,person=excluded.person,department=excluded.department,intro=excluded.intro,active=excluded.active',args)
                    data={'id':pid}
                elif path=='/api/admin/settings' and method=='POST':
                    b=json_body(env)
                    for k in ('title','subtitle'):
                        val=required_text(b,k,100)
                        db.execute('UPDATE settings SET value=? WHERE key=?',(val,k))
                    if b.get('remove_poster'): db.execute("UPDATE settings SET value='' WHERE key='poster'")
                    data={'ok':True}
                elif path=='/api/admin/poster' and method=='POST':
                    b=json_body(env)
                    try: raw=base64.b64decode(b.get('data',''),validate=True)
                    except Exception: raise ApiError('图片格式错误')
                    ext='png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else 'jpg' if raw.startswith(b'\xff\xd8\xff') else 'webp' if raw.startswith(b'RIFF') and raw[8:12]==b'WEBP' else None
                    if not ext or len(raw)>6*1024*1024: raise ApiError('请上传 6MB 以内的 PNG、JPG 或 WebP 图片')
                    filename=f'{uuid.uuid4().hex}.{ext}'
                    (DATA/'media'/filename).write_bytes(raw)
                    db.execute("UPDATE settings SET value=? WHERE key='poster'",('/media/'+filename,))
                    data={'ok':True}
                elif path=='/api/admin/export' and method=='GET':
                    data=excel_bytes(db); mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                    headers.append(('Content-Disposition','attachment; filename="AI-competition-scores.xlsx"'))
                else: raise ApiError('接口不存在',404)
            elif method in ('GET','HEAD'):
                if path in ('/','/admin','/admin/'):
                    file=ROOT/'static'/('admin.html' if path.startswith('/admin') else 'index.html')
                elif path.startswith('/static/'):
                    file=(ROOT/path.lstrip('/')).resolve()
                    if ROOT/'static' not in file.parents: raise ApiError('未找到页面',404)
                elif path.startswith('/media/'):
                    file=(DATA/'media'/path.split('/')[-1]).resolve()
                    if DATA/'media' not in file.parents: raise ApiError('文件不存在',404)
                else: raise ApiError('未找到页面',404)
                if not file.is_file(): raise ApiError('文件不存在',404)
                data=file.read_bytes(); mime=mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
                if mime.startswith('text/') or mime in ('application/javascript',): mime+='; charset=utf-8'
            else: raise ApiError('请求方法不支持',405)
        if not isinstance(data,bytes): data=json.dumps(data,ensure_ascii=False,allow_nan=False).encode('utf-8')
    except ApiError as e:
        status=e.status; data=json.dumps({'error':e.message},ensure_ascii=False).encode()
    except Exception:
        import traceback
        traceback.print_exc()
        status=500; data=json.dumps({'error':'服务暂时不可用，请稍后重试'},ensure_ascii=False).encode()
    phrase={200:'OK',400:'Bad Request',401:'Unauthorized',403:'Forbidden',404:'Not Found',405:'Method Not Allowed',413:'Payload Too Large',429:'Too Many Requests',500:'Internal Server Error'}[status]
    headers += [('Content-Type',mime),('Content-Length',str(len(data))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Referrer-Policy','same-origin')]
    start_response(f'{status} {phrase}',headers)
    return [b'' if method=='HEAD' else data]


init_db()

if __name__=='__main__':
    class ThreadedServer(ThreadingMixIn,WSGIServer):
        daemon_threads=True
    host=os.environ.get('HOST','127.0.0.1'); port=int(os.environ.get('PORT','8000'))
    print(f'Local: http://{host}:{port}',flush=True)
    print(f'Admin: http://{host}:{port}/admin',flush=True)
    if not os.environ.get('ADMIN_PASSWORD'): print(f'Temporary admin password: {ADMIN_PASSWORD}',flush=True)
    with make_server(host,port,application,server_class=ThreadedServer,handler_class=WSGIRequestHandler) as server:
        server.serve_forever()
