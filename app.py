"""AI 创新应用大赛。开发：python app.py；生产：waitress-serve app:application。"""
import base64
import hashlib
import hmac
import io
import json
import math
import mimetypes
import os
import re
from pathlib import Path
import secrets
import sqlite3
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from http.cookies import SimpleCookie
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server
from xml.sax.saxutils import escape
import wecom_auth
import competition
import event_rules

ROOT = Path(__file__).resolve().parent
# Local preview and Docker Compose share the same ignored password configuration.
if (ROOT / '.env').is_file():
    for config_line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines():
        config_key, separator, config_value = config_line.strip().partition('=')
        if separator and config_key in ('ADMIN_PASSWORD','WECOM_CORP_ID','WECOM_AGENT_ID','WECOM_SECRET','WECOM_PUBLIC_URL'):
            os.environ.setdefault(config_key,config_value.strip().strip('\"\''))
DATA = Path(os.environ.get('DATA_DIR', ROOT / 'data')).resolve()
DATA.mkdir(parents=True, exist_ok=True)
(DATA / 'media').mkdir(exist_ok=True)
DB = DATA / 'scores.sqlite3'
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD') or secrets.token_urlsafe(12)
COOKIE_SECURE = os.environ.get('COOKIE_SECURE', '0') == '1'
RUBRICS = json.loads((ROOT / 'rubrics.json').read_text(encoding='utf-8'))
CATEGORIES = {c['id']: c for c in RUBRICS}
FINAL_CATEGORIES = ('agent','solution')


@contextmanager
def connect(admin=False):
    path=DB if admin else competition.database(DB)
    db = sqlite3.connect(path if path==DB else 'file:'+path.as_posix()+'?mode=rw', timeout=15,uri=path!=DB)
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
        CREATE TABLE IF NOT EXISTS subscores(judge_id TEXT NOT NULL REFERENCES judges(id), project_id TEXT NOT NULL REFERENCES projects(id),
          dimension INTEGER NOT NULL, subdimension INTEGER NOT NULL, value INTEGER NOT NULL, updated TEXT NOT NULL,
          PRIMARY KEY(judge_id,project_id,dimension,subdimension));
        CREATE TABLE IF NOT EXISTS score_admin_log(id INTEGER PRIMARY KEY AUTOINCREMENT, judge_id TEXT NOT NULL, project_id TEXT NOT NULL, round TEXT NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL, updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS judge_access(judge_id TEXT PRIMARY KEY REFERENCES judges(id), nonce TEXT NOT NULL UNIQUE, enabled INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS judge_sessions(token TEXT PRIMARY KEY, judge_id TEXT NOT NULL REFERENCES judges(id), nonce TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS general_sessions(token TEXT PRIMARY KEY, judge_id TEXT NOT NULL REFERENCES judges(id), nonce TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS wecom_bindings(corp_id TEXT NOT NULL, userid TEXT NOT NULL, judge_id TEXT NOT NULL REFERENCES judges(id), PRIMARY KEY(corp_id,userid), UNIQUE(corp_id,judge_id));
        CREATE TABLE IF NOT EXISTS wecom_sessions(token TEXT PRIMARY KEY, corp_id TEXT NOT NULL, userid TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS wecom_states(token TEXT PRIMARY KEY, browser TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS write_versions(client TEXT NOT NULL, judge_id TEXT NOT NULL, project_id TEXT NOT NULL,
          dimension INTEGER NOT NULL, version INTEGER NOT NULL, PRIMARY KEY(client,judge_id,project_id,dimension));
        CREATE TABLE IF NOT EXISTS finalists(project_id TEXT PRIMARY KEY REFERENCES projects(id), preliminary_score REAL, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS ranking_projects(project_id TEXT PRIMARY KEY REFERENCES projects(id), included INTEGER NOT NULL, initial_source TEXT NOT NULL, initial_manual REAL, final_source TEXT NOT NULL, final_manual REAL, result_source TEXT NOT NULL, result_manual REAL);
        CREATE TABLE IF NOT EXISTS final_scores(judge_id TEXT NOT NULL REFERENCES judges(id), project_id TEXT NOT NULL REFERENCES projects(id),
          dimension INTEGER NOT NULL, value REAL NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(judge_id,project_id,dimension));
        CREATE TABLE IF NOT EXISTS final_subscores(judge_id TEXT NOT NULL REFERENCES judges(id), project_id TEXT NOT NULL REFERENCES projects(id),
          dimension INTEGER NOT NULL, subdimension INTEGER NOT NULL, value INTEGER NOT NULL, updated TEXT NOT NULL,
          PRIMARY KEY(judge_id,project_id,dimension,subdimension));
        CREATE TABLE IF NOT EXISTS final_write_versions(client TEXT NOT NULL, judge_id TEXT NOT NULL, project_id TEXT NOT NULL,
          dimension INTEGER NOT NULL, version INTEGER NOT NULL, PRIMARY KEY(client,judge_id,project_id,dimension));
        ''')
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES ('entry_signing_key',?)",(secrets.token_hex(32),))
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
    def __init__(self, message, status=400,code=None):
        self.message, self.status = message, status
        self.code=code


def json_body(env):
    if 'scoring.body' in env:return env['scoring.body']
    try:
        length = int(env.get('CONTENT_LENGTH') or 0)
        if length > 9 * 1024 * 1024:
            raise ApiError('文件过大，请使用 6MB 以内的图片',413)
        value = json.loads(env['wsgi.input'].read(length) or b'{}')
        if not isinstance(value,dict):
            raise ValueError()
        env['scoring.body']=value
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
    with connect(admin=True) as shared:
        if not shared.execute('SELECT 1 FROM sessions WHERE token=? AND expires>?',(hashed,time.time())).fetchone():
            raise ApiError('请先打开管理后台并验证管理密码',401)


def cookie_token(env,name):
    try:
        cookie=SimpleCookie();cookie.load(env.get('HTTP_COOKIE',''))
        return cookie[name].value if name in cookie else ''
    except Exception:
        return ''


def judge_session(env,db,required=False,claimed=None):
    token=cookie_token(env,'judge_session')
    row=db.execute('''SELECT j.id,j.name FROM judge_sessions s
        JOIN judges j ON j.id=s.judge_id JOIN judge_access a ON a.judge_id=j.id AND a.nonce=s.nonce
        WHERE s.token=? AND s.expires>? AND j.active=1 AND a.enabled=1''',
        (hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone() if token else None
    if not row:
        visitor=wecom_visitor(env,db)
        if visitor:
            row=db.execute('SELECT j.id,j.name FROM judges j JOIN wecom_bindings b ON b.judge_id=j.id WHERE b.corp_id=? AND b.userid=? AND j.active=1',(visitor['corp_id'],visitor['userid'])).fetchone()
    if not row and entry_mode(db)=='general' and token and not (visitor and db.execute('SELECT 1 FROM wecom_bindings WHERE corp_id=? AND userid=?',(visitor['corp_id'],visitor['userid'])).fetchone()):
        row=db.execute('''SELECT j.id,j.name FROM general_sessions s JOIN judges j ON j.id=s.judge_id
            JOIN judge_access a ON a.judge_id=j.id AND a.nonce=s.nonce WHERE s.token=? AND s.expires>? AND j.active=1 AND a.enabled=1''',(hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
    if not row:
        if required: raise ApiError('身份尚未确认，请返回首页进入评审',401)
        return None
    if claimed and claimed!=row['id']: raise ApiError('当前入口只能访问本人评分',403)
    return dict(row)


def entry_mode(db):
    row=db.execute("SELECT value FROM settings WHERE key='entry_mode'").fetchone()
    return 'general' if row and row['value']=='general' else 'bound'


def wecom_visitor(env,db):
    token=cookie_token(env,'wecom_session')
    if not token:return None
    row=db.execute('SELECT corp_id,userid FROM wecom_sessions WHERE token=? AND expires>? AND corp_id=?',(hashlib.sha256(token.encode()).hexdigest(),time.time(),wecom_auth.configuration()['corp'])).fetchone()
    return dict(row) if row else None


def auth_cookie(name,value,path='/api',age=604800):
    return ('Set-Cookie',f'{name}={value}; HttpOnly; SameSite=Lax; Path={path}; Max-Age={age}'+('; Secure' if COOKIE_SECURE else ''))


def entry_token(db,access):
    key=db.execute("SELECT value FROM settings WHERE key='entry_signing_key'").fetchone()['value']
    signature=hmac.new(bytes.fromhex(key),(access['judge_id']+':'+access['nonce']).encode(),hashlib.sha256).hexdigest()
    return access['nonce']+'.'+signature


def public_entry_url(value):
    if not isinstance(value,str) or len(value)>500: raise ApiError('请输入有效的评委入口网址')
    value=value.strip()
    if any(c.isspace() or ord(c)<32 or c=='\\' for c in value): raise ApiError('入口网址不能含空格或无效字符')
    try:
        parsed=urlparse(value)
        port=parsed.port
        hostname=parsed.hostname
    except ValueError: raise ApiError('网址端口无效')
    if parsed.scheme not in ('http','https') or not hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise ApiError('入口网址应为网站根地址，例如 https://score.example.com/')
    return parsed.scheme+'://'+parsed.netloc+'/'


def scoring_round(db):
    row=db.execute("SELECT value FROM settings WHERE key='scoring_round'").fetchone()
    return 'final' if row and row['value']=='final' else 'preliminary'


def event_rubrics(db):
    row=db.execute("SELECT value FROM settings WHERE key='event_rubrics'").fetchone()
    return json.loads(row['value']) if row else [{**c,'final_enabled':c['id'] in FINAL_CATEGORIES} for c in RUBRICS]


def event_categories(db):return {c['id']:c for c in event_rubrics(db)}


def final_categories(db):
    if not db.execute("SELECT 1 FROM settings WHERE key='event_rubrics'").fetchone():return list(FINAL_CATEGORIES)
    return [c['id'] for c in event_rubrics(db) if c.get('final_enabled',True)]


def final_enabled(db):
    row=db.execute("SELECT value FROM settings WHERE key='final_enabled'").fetchone()
    return not row or row['value']!='0'


def setting(db,key,value):
    db.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)))


def enable_final_category(db,cid):
    if cid not in final_categories(db):
        rules=event_rubrics(db)
        for category in rules:
            if category['id']==cid:category['final_enabled']=True
        setting(db,'event_rubrics',json.dumps(rules,ensure_ascii=False))
    setting(db,'final_enabled','1')


def score_tables(round_name):
    if round_name not in ('preliminary','final'): raise ApiError('比赛阶段无效')
    prefix='final_' if round_name=='final' else ''
    return prefix+'scores',prefix+'subscores',prefix+'write_versions'


def review_projects(db,round_name=None):
    round_name=round_name or scoring_round(db)
    if round_name=='final':
        rows=db.execute("SELECT p.* FROM projects p JOIN finalists f ON f.project_id=p.id WHERE p.active=1 AND f.active=1 ORDER BY p.category,p.number,p.rowid")
    else:
        rows=db.execute('SELECT * FROM projects WHERE active=1 ORDER BY category,number,rowid')
    return [dict(r) for r in rows if round_name!='final' or r['category'] in final_categories(db)]


def presentation_state(db):
    round_name=scoring_round(db)
    row=db.execute('SELECT value FROM settings WHERE key=?',('presenting_'+round_name,)).fetchone()
    project=next((p for p in review_projects(db,round_name) if row and p['id']==row['value']),None)
    return {'competition_id':competition.current.get(),'round':round_name,'project':{k:project[k] for k in ('id','category','number','title','person','department')} if project else None}


def clear_presentation(db,project_id,round_name=None):
    if round_name:
        db.execute("UPDATE settings SET value='' WHERE key=? AND value=?",('presenting_'+round_name,project_id))
    else:db.execute("UPDATE settings SET value='' WHERE key IN ('presenting_preliminary','presenting_final') AND value=?",(project_id,))


def snapshot(db):
    settings=dict(db.execute('SELECT key,value FROM settings WHERE key != "initialized"').fetchall())
    settings.pop('entry_signing_key',None)
    settings.pop('presenting_preliminary',None);settings.pop('presenting_final',None)
    settings.pop('event_rubrics',None)
    settings.setdefault('final_enabled','1');settings.setdefault('preliminary_weight','70');settings.setdefault('final_weight','30')
    overrides=json.loads(settings.pop('score_bands','{}'))
    subrules=json.loads(settings.pop('subitem_rules','{}'))
    categories=[{**c,'dimensions':[{**d,'bands':overrides.get(f"{c['id']}:{k}",d.get('bands',[])),
        'subitems':[{**sub,**subrules.get(f"{c['id']}:{k}:{si}",{})} for si,sub in enumerate(d.get('subitems',[]))]}
        for k,d in enumerate(c['dimensions'])]} for c in event_rubrics(db)]
    # Existing databases keep their settings; serve the optimized bundled poster.
    if settings.get('poster')=='/static/competition-poster.png':
        settings['poster']='/static/competition-poster-v1-1600.webp'
    return {
        'settings':settings,
        'categories':categories,
        'judges':[dict(r) for r in db.execute('SELECT j.*,COALESCE(a.enabled,0) AS entry_active FROM judges j LEFT JOIN judge_access a ON a.judge_id=j.id ORDER BY j.rowid')],
        'projects':[dict(r) for r in db.execute('SELECT * FROM projects ORDER BY category,number,rowid')],
        'round':scoring_round(db),
        'finalists':[dict(r) for r in db.execute('SELECT * FROM finalists')],
        'presentation':presentation_state(db),
        'competition_id':competition.current.get(),
        'final_categories':final_categories(db),
        'rules_version':int(settings.get('rules_version','0')),
    }


def all_records(db,round_name='preliminary',include_all=False):
    # Parent totals, child counts and manual initial marks share one read snapshot.
    if not db.in_transaction: db.execute('BEGIN')
    data = snapshot(db)
    scores_table,subs_table,_=score_tables(round_name)
    data['round']=round_name
    if round_name=='final' and not include_all:
        ids={r['project_id'] for r in data['finalists']}|{r[0] for r in db.execute('SELECT DISTINCT project_id FROM final_scores')}
        data['projects']=[p for p in data['projects'] if p['category'] in final_categories(db) and p['id'] in ids]
    rows = db.execute(f'SELECT * FROM {scores_table}').fetchall()
    scores = {}
    for r in rows:
        scores.setdefault((r['judge_id'],r['project_id']),{})[r['dimension']] = r['value']
    subs={}
    for r in db.execute(f'SELECT * FROM {subs_table}'):
        subs.setdefault((r['judge_id'],r['project_id']),{}).setdefault(str(r['dimension']),{})[str(r['subdimension'])]=r['value']
    records = []
    for p in data['projects']:
        dims = event_categories(db)[p['category']]['dimensions']
        for j in data['judges']:
            vals = scores.get((j['id'],p['id']),{})
            subvalues=subs.get((j['id'],p['id']),{})
            complete = len(vals) == len(dims) and all(str(k) not in subvalues or len(subvalues[str(k)])==len(d.get('subitems',[])) for k,d in enumerate(dims))
            records.append({'project_id':p['id'],'judge_id':j['id'],'values':{str(k):v for k,v in vals.items()},
              'subvalues':subvalues,
              'complete':complete,'total':round(sum(vals.values()),2) if complete else None,
              'subtotal':round(sum(vals.values()),2)})
    return data,records


def manage_judge_scores(db,b):
    db.execute('BEGIN IMMEDIATE')
    judge=required_text(b,'judge_id');stage=b.get('round');scores,subs,_=score_tables(stage)
    if not db.execute('SELECT 1 FROM judges WHERE id=?',(judge,)).fetchone():raise ApiError('评委不存在')
    data,records=all_records(db,stage,True)
    if b.get('rules_version')!=data['rules_version']:raise ApiError('评分规则已更新，请刷新后重试',409)
    action=b.get('action')
    if action not in ('save','clear_project','clear_judge'):raise ApiError('无效评分管理操作')
    reason=required_text(b,'reason',500) if action=='save' else '管理员确认清空评分'
    project=b.get('project_id')
    chosen=[r for r in records if r['judge_id']==judge and (action=='clear_judge' or r['project_id']==project)]
    if not chosen:raise ApiError('项目不存在')
    before={r['project_id']:{'values':r['values'],'subvalues':r['subvalues']} for r in chosen}
    if b.get('expected')!=before:raise ApiError('评分已被其他人修改，请重新打开评分后再操作',409)
    new_values={};new_subs={}
    if action=='save':
        p=next(p for p in data['projects'] if p['id']==project)
        dims=event_categories(db)[p['category']]['dimensions']
        values=b.get('values',{});subvalues=b.get('subvalues',{})
        if not isinstance(values,dict) or not isinstance(subvalues,dict):raise ApiError('评分格式无效')
        if (set(values)|set(subvalues))-{str(k) for k in range(len(dims))}:raise ApiError('无效评分项')
        def checked(value,limit):
            if type(value) not in (int,float) or not math.isfinite(value) or value!=int(value) or not 0<=value<=limit:raise ApiError(f'分数应为0～{limit}的整数')
            return int(value)
        for k,d in enumerate(dims):
            key=str(k);children=subvalues.get(key,{})
            if not isinstance(children,dict):raise ApiError('子项评分格式无效')
            if children:
                rules=d.get('subitems',[])
                if set(children)-{str(i) for i in range(len(rules))}:raise ApiError('无效子评分项')
                new_subs[key]={si:checked(value,rules[int(si)]['max']) for si,value in children.items()}
                new_values[key]=sum(new_subs[key].values())
                if key in values and values[key]!=new_values[key]:raise ApiError('评分项总分必须等于子项合计')
            elif key in values:new_values[key]=checked(values[key],d['max'])
    stamp=now();changed=0
    for r in chosen:
        pid=r['project_id'];after={'values':new_values,'subvalues':new_subs} if action=='save' else {'values':{},'subvalues':{}}
        if before[pid]==after:continue
        for table in (scores,subs):db.execute(f'DELETE FROM {table} WHERE judge_id=? AND project_id=?',(judge,pid))
        for k,value in after['values'].items():db.execute(f'INSERT INTO {scores} VALUES (?,?,?,?,?)',(judge,pid,int(k),value,stamp))
        for k,children in after['subvalues'].items():
            for si,value in children.items():db.execute(f'INSERT INTO {subs} VALUES (?,?,?,?,?,?)',(judge,pid,int(k),int(si),value,stamp))
        db.execute('INSERT INTO score_admin_log(judge_id,project_id,round,action,reason,before_json,after_json,updated) VALUES (?,?,?,?,?,?,?,?)',(judge,pid,stage,action,reason,json.dumps(before[pid],ensure_ascii=False),json.dumps(after,ensure_ascii=False),stamp))
        changed+=1
    return {'ok':True,'changed':changed}


def ranking_settings(db):
    settings=dict(db.execute('SELECT key,value FROM settings'))
    categories=list(event_categories(db))
    selected=json.loads(settings.get('leaderboard_categories','null'))
    return {'categories':[cid for cid in (selected if selected is not None else (final_categories(db) if final_enabled(db) else categories)) if cid in categories],
        'mode':settings.get('leaderboard_mode','weighted' if final_enabled(db) else 'preliminary'),
        'preliminary_weight':int(settings.get('preliminary_weight','70')),'final_weight':int(settings.get('final_weight','30')),
        'scroll_enabled':settings.get('leaderboard_scroll_enabled','1')=='1','scroll_speed':int(settings.get('leaderboard_scroll_speed','16')),
        'scroll_top_hold':int(settings.get('leaderboard_scroll_top_hold','12')),'scroll_bottom_hold':int(settings.get('leaderboard_scroll_bottom_hold','3'))}


def ranking_projects(db):
    configured={r['project_id']:dict(r) for r in db.execute('SELECT * FROM ranking_projects')}
    legacy={r['project_id']:dict(r) for r in db.execute('SELECT * FROM finalists')}
    multi=final_enabled(db)
    return [{**{'project_id':p['id'],'included':int(bool(legacy.get(p['id'],{}).get('active'))) if multi else 1,
        'initial_source':'manual' if legacy.get(p['id'],{}).get('preliminary_score') is not None else 'system','initial_manual':legacy.get(p['id'],{}).get('preliminary_score'),
        'final_source':'system','final_manual':None,'result_source':'computed','result_manual':None},**configured.get(p['id'],{})}
        for p in db.execute('SELECT id FROM projects')]


def leaderboard(db,private=False):
    multi=final_enabled(db)
    data,initial_records=all_records(db,'preliminary',True)
    _,final_records=all_records(db,'final',True)
    options=ranking_settings(db);configs={r['project_id']:r for r in ranking_projects(db)}
    required_judges={j['id'] for j in data['judges'] if j['active']}
    def average(records,pid):
        done=[r for r in records if r['project_id']==pid and r['complete'] and r['judge_id'] in required_judges]
        ready=bool(required_judges) and required_judges.issubset({r['judge_id'] for r in done})
        return (sum((Decimal(str(r['total'])) for r in done),Decimal(0))/len(done) if ready else None),len(done)
    def rounded(value):
        return float(value.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)) if value is not None else None
    groups=[]
    for cid in options['categories']:
        rows=[]
        for p in data['projects']:
            config=configs[p['id']]
            if p['category']!=cid or not p['active'] or not config['included']: continue
            initial_actual,initial_count=average(initial_records,p['id']);final_actual,final_count=average(final_records,p['id'])
            initial=initial_actual if config['initial_source']=='system' else (Decimal(str(config['initial_manual'])) if config['initial_manual'] is not None else None)
            final=final_actual if config['final_source']=='system' else (Decimal(str(config['final_manual'])) if config['final_manual'] is not None else None)
            first=Decimal(options['preliminary_weight'])/100;second=Decimal(options['final_weight'])/100
            mode=options['mode']
            if config['result_source']=='manual' or mode=='manual':combined=Decimal(str(config['result_manual'])) if config['result_manual'] is not None else None
            elif mode=='preliminary':combined=initial
            elif mode=='final':combined=final
            else:combined=(initial or Decimal(0))*first+(final or Decimal(0))*second if (initial is not None or not first) and (final is not None or not second) else None
            row={'id':p['id'],'number':p['number'],'title':p['title'],'person':p['person'],'department':p['department'],'score':rounded(combined),'rank':None}
            if private: row.update(preliminary_score=rounded(initial),final_average=rounded(final),completed_judges=final_count if multi else initial_count,initial_judges=initial_count,final_judges=final_count,**config)
            rows.append(row)
        rows.sort(key=lambda r:(r['score'] is None,-(r['score'] or 0),r['number'],r['id']))
        previous=None
        for i,row in enumerate(rows,1):
            if row['score'] is not None:
                row['rank']=previous['rank'] if previous and previous['score']==row['score'] else i
                previous=row
            row.pop('number')
        groups.append({'id':cid,'name':event_categories(db)[cid]['name'],'projects':rows})
    return {'categories':groups,'updated':now(),'title':data['settings']['title'],'round':'preliminary' if options['mode']=='preliminary' else 'final','score_caption':{'preliminary':'初试成绩','final':'复试成绩','weighted':'综合成绩','manual':'最终成绩'}[options['mode']],'scroll_settings':{k:v for k,v in options.items() if k.startswith('scroll_')},'competition_id':competition.current.get()}


def excel_bytes(db):
    from excel_export import build_report
    data, records = all_records(db,scoring_round(db))
    if data['round']=='final':
        data['combined_scores']={p['id']:p for group in leaderboard(db,True)['categories'] for p in group['projects']}
        data['categories']=[c for c in data['categories'] if c['id'] in final_categories(db)]
    return build_report(data, records, data['categories'])


LOGIN_FAILURES = {}


def _application(env,start_response):
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
            if method in ('POST','PUT','DELETE') and path.startswith('/api/') and path not in ('/api/admin/login','/api/judge/enter'):
                if json_body(env).get('competition_id','default')!=competition.current.get():raise ApiError('当前比赛已切换，请刷新页面后继续',409,code='competition_changed')
            if method=='GET' and path.startswith('/api/admin/') and path!='/api/admin/state':
                expected=parse_qs(env.get('QUERY_STRING','')).get('competition_id',[competition.current.get()])[0]
                if expected!=competition.current.get():raise ApiError('当前比赛已切换，请刷新后台后重试',409)
            if path=='/api/wecom/start' and method=='GET':
                if not wecom_auth.configuration()['ready']:raise ApiError('企业微信尚未配置，请联系管理员')
                state=secrets.token_urlsafe(24);browser=secrets.token_urlsafe(24)
                db.execute('DELETE FROM wecom_states WHERE expires<?',(time.time(),))
                db.execute('INSERT INTO wecom_states VALUES (?,?,?)',(hashlib.sha256(state.encode()).hexdigest(),hashlib.sha256(browser.encode()).hexdigest(),time.time()+300))
                headers.extend([auth_cookie('wecom_oauth',browser,'/api/wecom',300),('Location',wecom_auth.authorization_url(state))])
                status=302;mime='text/html; charset=utf-8';data=b''
            elif path=='/api/wecom/callback' and method=='GET':
                headers.extend([auth_cookie('wecom_oauth','','/api/wecom',0),auth_cookie('judge_session','',age=0),auth_cookie('wecom_session','',age=0)])
                query=parse_qs(env.get('QUERY_STRING',''));state=query.get('state',[''])[0];code=query.get('code',[''])[0]
                browser=cookie_token(env,'wecom_oauth');error='state'
                state_hash=hashlib.sha256(state.encode()).hexdigest()
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT browser,expires FROM wecom_states WHERE token=?',(state_hash,)).fetchone()
                valid=bool(row and state and browser and row['expires']>time.time() and hmac.compare_digest(row['browser'],hashlib.sha256(browser.encode()).hexdigest()))
                if valid:db.execute('DELETE FROM wecom_states WHERE token=?',(state_hash,))
                db.commit()
                if valid and code and len(code)<=1024:
                    try:
                        corp,userid=wecom_auth.identify(code)
                        token=secrets.token_urlsafe(32)
                        db.execute('DELETE FROM wecom_sessions WHERE expires<?',(time.time(),))
                        db.execute('INSERT INTO wecom_sessions VALUES (?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),corp,userid,time.time()+604800))
                        headers.append(auth_cookie('wecom_session',token));error=''
                    except wecom_auth.WecomError as failure:error=str(failure)
                elif valid:error='cancelled'
                status=302;mime='text/html; charset=utf-8';headers.append(('Location','/?wecom='+('ok' if not error else error)));data=b''
            elif path=='/api/judge/select' and method=='POST':
                if entry_mode(db)!='general':raise ApiError('当前仅允许绑定身份或专属入口进入',403)
                jid=required_text(json_body(env),'judge_id')
                visitor=wecom_visitor(env,db)
                bound=db.execute('SELECT judge_id FROM wecom_bindings WHERE corp_id=? AND userid=?',(visitor['corp_id'],visitor['userid'])).fetchone() if visitor else None
                if bound and bound['judge_id']!=jid:raise ApiError('企业微信已绑定评委，只能使用本人身份',403)
                if not db.execute('SELECT 1 FROM judges WHERE id=? AND active=1',(jid,)).fetchone():raise ApiError('评委不存在或已停用')
                access=db.execute('SELECT * FROM judge_access WHERE judge_id=?',(jid,)).fetchone()
                if not access or not access['enabled']:
                    db.execute('INSERT INTO judge_access VALUES (?,?,1) ON CONFLICT(judge_id) DO UPDATE SET nonce=excluded.nonce,enabled=1',(jid,secrets.token_urlsafe(24)))
                    access=db.execute('SELECT * FROM judge_access WHERE judge_id=?',(jid,)).fetchone()
                session=secrets.token_urlsafe(32)
                db.execute('DELETE FROM general_sessions WHERE expires<?',(time.time(),))
                db.execute('INSERT INTO general_sessions VALUES (?,?,?,?)',(hashlib.sha256(session.encode()).hexdigest(),jid,access['nonce'],time.time()+604800))
                headers.append(auth_cookie('judge_session',session));data={'ok':True}
            elif path=='/api/judge/enter' and method=='POST':
                # A failed scan must not silently retain another judge's identity.
                headers.append(('Set-Cookie','judge_session=; HttpOnly; SameSite=Strict; Path=/api; Max-Age=0'+('; Secure' if COOKIE_SECURE else '')))
                token=required_text(json_body(env),'token',200)
                nonce,separator,_=token.partition('.')
                access=db.execute('SELECT a.* FROM judge_access a JOIN judges j ON j.id=a.judge_id WHERE a.nonce=? AND a.enabled=1 AND j.active=1',(nonce,)).fetchone() if separator else None
                if not access or not hmac.compare_digest(entry_token(db,access),token): raise ApiError('二维码已失效或评委已停用，请联系管理员',401)
                session=secrets.token_urlsafe(32)
                db.execute('DELETE FROM judge_sessions WHERE expires<?',(time.time(),))
                db.execute('INSERT INTO judge_sessions VALUES (?,?,?,?)',(hashlib.sha256(session.encode()).hexdigest(),access['judge_id'],access['nonce'],time.time()+604800))
                headers.append(('Set-Cookie',f'judge_session={session}; HttpOnly; SameSite=Strict; Path=/api; Max-Age=604800'+('; Secure' if COOKIE_SECURE else '')))
                data={'ok':True}
            elif path=='/api/bootstrap' and method=='GET':
                db.execute('BEGIN')
                data=snapshot(db)
                current=judge_session(env,db)
                data['current_judge']=current
                data['judges']=[current] if current else ([dict(r) for r in db.execute('SELECT id,name FROM judges WHERE active=1 ORDER BY rowid')] if entry_mode(db)=='general' else [])
                visitor=wecom_visitor(env,db)
                bound=db.execute('SELECT judge_id FROM wecom_bindings WHERE corp_id=? AND userid=?',(visitor['corp_id'],visitor['userid'])).fetchone() if visitor else None
                data['entry']={'mode':entry_mode(db),'wecom_enabled':wecom_auth.configuration()['ready'],'wecom_userid':visitor['userid'] if visitor else '', 'wecom_bound':bool(bound)}
                data['projects']=[p for p in data['projects'] if p['active']] if current else []
                if data['round']=='final':
                    ids={r['project_id'] for r in data['finalists'] if r['active']}
                    data['projects']=[p for p in data['projects'] if p['id'] in ids and p['category'] in final_categories(db)]
                    data['categories']=[c for c in data['categories'] if c['id'] in final_categories(db)]
                data.pop('finalists')
                if not current:data['presentation']={'competition_id':competition.current.get(),'round':data['round'],'project':None}
            elif path=='/api/presentation' and method=='GET':
                db.execute('BEGIN')
                judge_session(env,db,True)
                data=presentation_state(db)
            elif path=='/api/leaderboard' and method=='GET':
                data=leaderboard(db)
            elif path=='/api/scores' and method=='GET':
                query=parse_qs(env.get('QUERY_STRING',''))
                judge=judge_session(env,db,True,query.get('judge',[''])[0])['id']
                round_name=query.get('round',[scoring_round(db)])[0]
                scores_table,subs_table,_=score_tables(round_name)
                data={'round':round_name,'scores':[dict(r) for r in db.execute(f'SELECT project_id,dimension,value,updated FROM {scores_table} WHERE judge_id=?',(judge,))],
                      'subscores':[dict(r) for r in db.execute(f'SELECT project_id,dimension,subdimension,value,updated FROM {subs_table} WHERE judge_id=?',(judge,))]}
            elif path=='/api/score' and method=='PUT':
                b=json_body(env)
                db.execute('BEGIN IMMEDIATE')
                current_version=db.execute("SELECT value FROM settings WHERE key='rules_version'").fetchone()
                if b.get('rules_version',0)!=(int(current_version[0]) if current_version else 0):raise ApiError('评分结构已更新，请刷新页面后再评分',409)
                judge=judge_session(env,db,True,b.get('judge_id'))['id']; project=required_text(b,'project_id')
                round_name=b.get('round','preliminary')
                scores_table,subs_table,versions_table=score_tables(round_name)
                if round_name!=scoring_round(db): raise ApiError('比赛阶段已切换，请刷新页面后继续评分',409)
                p=db.execute('SELECT * FROM projects WHERE id=? AND active=1',(project,)).fetchone()
                if not p: raise ApiError('项目不存在或已停用',code='project_unavailable')
                if round_name=='final' and (p['category'] not in final_categories(db) or not db.execute('SELECT 1 FROM finalists WHERE project_id=? AND active=1',(project,)).fetchone()): raise ApiError('该项目未入围复试',403,code='project_unavailable')
                k=b.get('dimension'); v=b.get('value')
                dims=event_categories(db)[p['category']]['dimensions']
                if type(k) is not int or k<0 or k>=len(dims): raise ApiError('无效评分项')
                sub=b.get('subdimension'); limit=dims[k]['max']
                if 'subdimension' in b:
                    if type(sub) is not int or not 0<=sub<len(dims[k].get('subitems',[])): raise ApiError('无效子评分项')
                    limit=dims[k]['subitems'][sub]['max']
                elif db.execute(f'SELECT 1 FROM {subs_table} WHERE judge_id=? AND project_id=? AND dimension=?',(judge,project,k)).fetchone():
                    raise ApiError('该项已按子项评分，请分别修改子项分数')
                if v is not None and (type(v) not in (int,float) or not math.isfinite(v) or v<0 or v>limit or v!=int(v)):
                    raise ApiError(f"分数应为 0～{limit} 的整数")
                stamp=now()
                apply_write=True
                if 'client' in b:
                    client=required_text(b,'client',100); version=b.get('version')
                    if type(version) is not int or version<0 or version>9007199254740991: raise ApiError('同步版本无效')
                    version_dimension=k if sub is None else 1000+k*100+sub
                    result=db.execute(f'INSERT INTO {versions_table} VALUES (?,?,?,?,?) ON CONFLICT(client,judge_id,project_id,dimension) DO UPDATE SET version=excluded.version WHERE excluded.version>{versions_table}.version',(client,judge,project,version_dimension,version))
                    apply_write=result.rowcount>0
                if not apply_write:
                    pass
                elif sub is not None:
                    if v is None:
                        db.execute(f'DELETE FROM {subs_table} WHERE judge_id=? AND project_id=? AND dimension=? AND subdimension=?',(judge,project,k,sub))
                    else:
                        db.execute(f'INSERT INTO {subs_table} VALUES (?,?,?,?,?,?) ON CONFLICT(judge_id,project_id,dimension,subdimension) DO UPDATE SET value=excluded.value,updated=excluded.updated',(judge,project,k,sub,v,stamp))
                    partial=db.execute(f'SELECT COUNT(*) AS n,SUM(value) AS total FROM {subs_table} WHERE judge_id=? AND project_id=? AND dimension=?',(judge,project,k)).fetchone()
                    if partial['n']:
                        db.execute(f'INSERT INTO {scores_table} VALUES (?,?,?,?,?) ON CONFLICT(judge_id,project_id,dimension) DO UPDATE SET value=excluded.value,updated=excluded.updated',(judge,project,k,partial['total'],stamp))
                    else:
                        db.execute(f'DELETE FROM {scores_table} WHERE judge_id=? AND project_id=? AND dimension=?',(judge,project,k))
                elif v is None:
                    db.execute(f'DELETE FROM {scores_table} WHERE judge_id=? AND project_id=? AND dimension=?',(judge,project,k))
                else:
                    db.execute(f'INSERT INTO {scores_table} VALUES (?,?,?,?,?) ON CONFLICT(judge_id,project_id,dimension) DO UPDATE SET value=excluded.value,updated=excluded.updated',(judge,project,k,v,stamp))
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
                with connect(admin=True) as shared:
                    shared.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
                    shared.execute('INSERT INTO sessions VALUES (?,?)',(hashlib.sha256(token.encode()).hexdigest(),time.time()+43200))
                headers.append(('Set-Cookie',f'admin_session={token}; HttpOnly; SameSite=Strict; Path=/api/admin; Max-Age=43200'+('; Secure' if COOKIE_SECURE else '')))
                data={'ok':True}
            elif path.startswith('/api/admin/'):
                admin_session(env,db)
                if path=='/api/admin/state' and method=='GET':
                    db.execute('BEGIN')
                    data=snapshot(db)
                    _,records=all_records(db,data['round']); data['records']=records
                    data['leaderboard']=leaderboard(db,True)
                    data['ranking_settings']=ranking_settings(db)
                    data['ranking_projects']=ranking_projects(db)
                    data['competitions']=competition.listing()
                    data['scored_categories']=[r[0] for r in db.execute('SELECT DISTINCT p.category FROM projects p JOIN (SELECT project_id FROM scores UNION SELECT project_id FROM final_scores) s ON s.project_id=p.id')]
                    config=wecom_auth.configuration()
                    data['wecom']={'configured':config['ready'],'corp_id':config['corp'],'agent_id':config['agent'],'callback_url':config['base']+'/api/wecom/callback' if config['base'] else '', 'mode':entry_mode(db),
                        'bindings':[dict(r) for r in db.execute('SELECT userid,judge_id FROM wecom_bindings WHERE corp_id=?',(config['corp'],))],
                        'verification_files':sorted({p.name for folder in (DATA/'wecom-verification',ROOT/'static/wecom-verification') for p in folder.glob('WW_verify_*.txt')})}
                elif path=='/api/admin/judge-scores' and method=='GET':
                    args=parse_qs(env.get('QUERY_STRING',''))
                    jid=args.get('judge_id',[''])[0];stage=args.get('round',['preliminary'])[0]
                    score_tables(stage)
                    if not db.execute('SELECT 1 FROM judges WHERE id=?',(jid,)).fetchone():raise ApiError('评委不存在')
                    _,records=all_records(db,stage,True)
                    data={'round':stage,'judge_id':jid,'competition_id':competition.current.get(),'rules_version':snapshot(db)['rules_version'],'records':[r for r in records if r['judge_id']==jid],
                        'history':[dict(r) for r in db.execute('SELECT id,project_id,action,reason,updated FROM score_admin_log WHERE judge_id=? AND round=? ORDER BY id DESC LIMIT 50',(jid,stage))]}
                elif path=='/api/admin/judge-scores' and method=='POST':
                    b=json_body(env);data=manage_judge_scores(db,b)
                elif path=='/api/admin/competitions' and method=='POST':
                    b=json_body(env)
                    if b.get('action')=='switch':
                        identifier=required_text(b,'id',40)
                        try:competition.activate(identifier)
                        except ValueError as error:raise ApiError(str(error))
                        data={'ok':True,'competition_id':identifier}
                    elif b.get('action')=='create':
                        title=required_text(b,'title',100);identifier=competition.identifier()
                        source=snapshot(db)
                        rules=source['categories'] if b.get('copy_rules',True) else [{'id':'general','name':'综合评审','final_enabled':True,'dimensions':[{'name':'综合表现','max':100,'description':'根据参赛内容综合评分。','bands':[],'subitems':[{'name':'综合表现','max':100,'definition':'依据以下核对要素进行综合评分。','elements':['内容完整','结果准确','方案合理','表达清晰','具有实际价值'],'scoring_rule':'结合核对要素与实际完成情况，在本项满分范围内给出整数分。'}]}]}]
                        token=competition.current.set(identifier)
                        try:
                            file=competition.database(DB);file.touch(mode=0o600,exist_ok=False)
                            with sqlite3.connect(file) as fresh:
                                fresh.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                                fresh.executemany('INSERT INTO settings VALUES (?,?)',[('initialized','1'),('title',title),('subtitle',''),('poster',''),('event_rubrics',json.dumps(rules,ensure_ascii=False)),('final_enabled','0'),('entry_mode','bound')])
                            init_db()
                        finally:competition.current.reset(token)
                        competition.register(identifier,title);data={'id':identifier}
                    else:raise ApiError('比赛操作无效')
                elif path=='/api/admin/competition-settings' and method=='POST':
                    b=json_body(env);title=required_text(b,'title',100);subtitle=b.get('subtitle','')
                    if not isinstance(subtitle,str) or len(subtitle)>200:raise ApiError('副标题最多200字')
                    first=b.get('preliminary_weight',70);second=b.get('final_weight',30)
                    if type(first) is not int or type(second) is not int or first<0 or second<0 or first+second!=100:raise ApiError('两阶段权重应为非负整数且合计100%')
                    enabled=bool(b.get('final_enabled'))
                    if not enabled and scoring_round(db)=='final':raise ApiError('请先切回初试，再关闭复试')
                    if enabled and not final_categories(db):raise ApiError('请先为至少一个分类启用复试')
                    setting(db,'title',title);setting(db,'subtitle',subtitle);setting(db,'final_enabled','1' if enabled else '0');setting(db,'preliminary_weight',first);setting(db,'final_weight',second)
                    setting(db,'show_intro','1' if b.get('show_intro') else '0')
                    competition.rename(title);data={'ok':True}
                elif path=='/api/admin/categories' and method=='POST':
                    try:rules=event_rules.validate(json_body(env).get('categories'))
                    except ValueError as error:raise ApiError(str(error))
                    old=event_categories(db);new={c['id']:c for c in rules}
                    for cid in old:
                        if cid not in new and db.execute('SELECT 1 FROM projects WHERE category=?',(cid,)).fetchone():raise ApiError('分类已有参赛项目，不能删除；请新建比赛或先调整未评分项目')
                        scored=db.execute('SELECT 1 FROM projects p JOIN (SELECT project_id FROM scores UNION SELECT project_id FROM final_scores) s ON s.project_id=p.id WHERE p.category=?',(cid,)).fetchone()
                        if scored and (cid not in new or event_rules.structure([old[cid]])!=event_rules.structure([new[cid]])):raise ApiError('该分类已有评分，不能修改分值或增删评分项；文字细则可修改，其他规则请新建比赛')
                        if cid in new and old[cid].get('final_enabled',True)!=new[cid]['final_enabled'] and db.execute('SELECT 1 FROM finalists f JOIN projects p ON p.id=f.project_id WHERE p.category=?',(cid,)).fetchone():raise ApiError('该分类已有复试配置，不能修改是否参加复试')
                    if final_enabled(db) and not any(c['final_enabled'] for c in rules):raise ApiError('复试已启用，至少保留一个复试分类，或先关闭复试')
                    # Incorporate existing overrides into the submitted snapshot, then clear them.
                    setting(db,'event_rubrics',json.dumps(rules,ensure_ascii=False))
                    if event_rules.structure(list(old.values()))!=event_rules.structure(rules):
                        row=db.execute("SELECT value FROM settings WHERE key='rules_version'").fetchone();setting(db,'rules_version',(int(row[0]) if row else 0)+1)
                    db.execute("DELETE FROM settings WHERE key IN ('score_bands','subitem_rules')")
                    data={'ok':True}
                elif path=='/api/admin/entry-mode' and method=='POST':
                    mode=json_body(env).get('mode')
                    if mode not in ('general','bound'):raise ApiError('入口模式无效')
                    if mode!=entry_mode(db):db.execute('DELETE FROM general_sessions')
                    db.execute("INSERT INTO settings VALUES ('entry_mode',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(mode,))
                    data={'ok':True,'mode':mode}
                elif path=='/api/admin/wecom-verification' and method=='POST':
                    b=json_body(env);filename=b.get('filename','')
                    if not isinstance(filename,str) or not re.fullmatch(r'WW_verify_[A-Za-z0-9_-]{1,100}\.txt',filename):raise ApiError('请选择企业微信下载的原始WW_verify校验txt文件')
                    try:content=base64.b64decode(b.get('data',''),validate=True)
                    except (ValueError,TypeError):raise ApiError('校验文件无法读取')
                    if not 1<=len(content)<=4096 or any(c<32 and c not in (9,10,13) for c in content):raise ApiError('校验文件应为4KB以内的原始文本')
                    folder=DATA/'wecom-verification';folder.mkdir(exist_ok=True);(folder/filename).write_bytes(content)
                    data={'ok':True,'path':'/'+filename}
                elif path=='/api/admin/presentation' and method=='POST':
                    b=json_body(env);db.execute('BEGIN IMMEDIATE')
                    current=presentation_state(db);round_name=current['round']
                    if b.get('round')!=round_name:raise ApiError('比赛阶段已变化，请刷新管理后台',409)
                    current_id=current['project']['id'] if current['project'] else None
                    if 'expected_project_id' in b and b['expected_project_id']!=current_id:raise ApiError('宣讲项目已被切换，请刷新后再操作',409)
                    action=b.get('action','select');target=None
                    if action=='select':
                        pid=required_text(b,'project_id')
                        target=next((p for p in review_projects(db) if p['id']==pid),None)
                        if not target:raise ApiError('该项目不在当前阶段的可评项目中')
                    elif action in ('previous','next'):
                        category=b.get('category') or (current['project']['category'] if current['project'] else None)
                        sequence=[p for p in review_projects(db) if p['category']==category]
                        if not sequence:raise ApiError('当前模块没有可宣讲的项目')
                        index=next((i for i,p in enumerate(sequence) if p['id']==current_id),None)
                        target_index=(0 if action=='next' else len(sequence)-1) if index is None else index+(1 if action=='next' else -1)
                        if not 0<=target_index<len(sequence):raise ApiError('已经是本模块最后一个项目' if action=='next' else '已经是本模块第一个项目')
                        target=sequence[target_index]
                    elif action!='clear':raise ApiError('宣讲操作无效')
                    db.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('presenting_'+round_name,target['id'] if target else ''))
                    data=presentation_state(db)
                elif path=='/api/admin/ranking-settings' and method=='POST':
                    b=json_body(env);selected=b.get('categories');mode=b.get('mode')
                    if not isinstance(selected,list) or any(not isinstance(cid,str) or cid not in event_categories(db) for cid in selected) or len(set(selected))!=len(selected):raise ApiError('请选择有效的排行榜模块')
                    if mode not in ('preliminary','final','weighted','manual'):raise ApiError('排行榜计分方式无效')
                    first=b.get('preliminary_weight');second=b.get('final_weight')
                    if type(first) is not int or type(second) is not int or first<0 or second<0 or first+second!=100:raise ApiError('初试和复试比例必须为非负整数且合计100%')
                    scrolling=ranking_settings(db)
                    for name,minimum,maximum in [('scroll_speed',8,40),('scroll_top_hold',0,120),('scroll_bottom_hold',0,30)]:
                        value=b.get(name,scrolling[name])
                        if type(value) is not int or not minimum<=value<=maximum:raise ApiError(f'滚动参数应为整数，速度8～40，顶部停留0～120秒，底部停留0～30秒')
                        scrolling[name]=value
                    scrolling['scroll_enabled']=b.get('scroll_enabled',scrolling['scroll_enabled'])
                    if type(scrolling['scroll_enabled']) is not bool:raise ApiError('请选择是否启用自动滚动')
                    setting(db,'leaderboard_categories',json.dumps(selected));setting(db,'leaderboard_mode',mode)
                    setting(db,'preliminary_weight',first);setting(db,'final_weight',second)
                    for name in ['scroll_speed','scroll_top_hold','scroll_bottom_hold','scroll_enabled']:setting(db,'leaderboard_'+name,int(scrolling[name]))
                    data={'ok':True}
                elif path=='/api/admin/ranking-project' and method=='POST':
                    b=json_body(env);pid=required_text(b,'project_id')
                    p=db.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone()
                    if not p:raise ApiError('项目不存在',404)
                    maximum=sum(d['max'] for d in event_categories(db)[p['category']]['dimensions'])
                    values=[]
                    for key in ('initial_manual','final_manual','result_manual'):
                        value=b.get(key)
                        if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=maximum or Decimal(str(value))*100!=(Decimal(str(value))*100).to_integral_value()):raise ApiError(f'分数应为0～{maximum}，最多保留两位小数')
                        values.append(value)
                    if b.get('initial_source') not in ('manual','system') or b.get('final_source') not in ('manual','system') or b.get('result_source') not in ('manual','computed'):raise ApiError('请选择有效的分数来源')
                    if type(b.get('included')) is not bool:raise ApiError('请选择是否上榜')
                    db.execute('INSERT INTO ranking_projects VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET included=excluded.included,initial_source=excluded.initial_source,initial_manual=excluded.initial_manual,final_source=excluded.final_source,final_manual=excluded.final_manual,result_source=excluded.result_source,result_manual=excluded.result_manual',
                        (pid,int(b['included']),b['initial_source'],values[0],b['final_source'],values[1],b['result_source'],values[2]))
                    data={'ok':True}
                elif path=='/api/admin/project-batch' and method=='POST':
                    b=json_body(env);cid=required_text(b,'category');ids=b.get('project_ids');action=b.get('action')
                    if cid not in event_categories(db):raise ApiError('项目模块不存在')
                    if not isinstance(ids,list) or not 1<=len(ids)<=500 or any(not isinstance(pid,str) or not pid for pid in ids) or len(set(ids))!=len(ids):raise ApiError('请勾选1～500个不同项目')
                    if action not in ('finalists_add','finalists_remove','ranking_add','ranking_remove','use_system_scores'):raise ApiError('批量操作无效')
                    include_ranking=b.get('include_ranking',True);use_system=b.get('use_system_scores',True)
                    if type(include_ranking) is not bool or type(use_system) is not bool:raise ApiError('批量选项无效')
                    placeholders=','.join('?' for _ in ids)
                    projects=[dict(r) for r in db.execute(f'SELECT * FROM projects WHERE id IN ({placeholders})',ids)]
                    if len(projects)!=len(ids) or any(p['category']!=cid for p in projects):raise ApiError('勾选项目不属于当前模块，请刷新后重选')
                    if action in ('finalists_add','ranking_add') and any(not p['active'] for p in projects):raise ApiError('停用项目不能加入，请先启用或取消勾选')
                    configs={r['project_id']:r for r in ranking_projects(db)}
                    if action=='finalists_add':enable_final_category(db,cid)
                    for pid in ids:
                        config=configs[pid]
                        if action=='finalists_add':
                            db.execute('INSERT INTO finalists VALUES (?,?,1) ON CONFLICT(project_id) DO UPDATE SET active=1',(pid,config['initial_manual']))
                        elif action=='finalists_remove':
                            db.execute('UPDATE finalists SET active=0 WHERE project_id=?',(pid,));clear_presentation(db,pid,'final')
                        if action=='ranking_add' or (action=='finalists_add' and include_ranking):config['included']=1
                        elif action=='ranking_remove':config['included']=0
                        if action=='use_system_scores' or (action in ('finalists_add','ranking_add') and use_system):
                            config.update(initial_source='system',final_source='system',result_source='computed')
                        db.execute('INSERT INTO ranking_projects VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET included=excluded.included,initial_source=excluded.initial_source,initial_manual=excluded.initial_manual,final_source=excluded.final_source,final_manual=excluded.final_manual,result_source=excluded.result_source,result_manual=excluded.result_manual',
                            tuple(config[k] for k in ['project_id','included','initial_source','initial_manual','final_source','final_manual','result_source','result_manual']))
                    if action=='ranking_add' or (action=='finalists_add' and include_ranking):
                        categories=ranking_settings(db)['categories']
                        if cid not in categories:categories.append(cid)
                        setting(db,'leaderboard_categories',json.dumps(categories))
                    data={'ok':True,'count':len(ids),'action':action}
                elif path=='/api/admin/finalists' and method=='POST':
                    b=json_body(env);pid=required_text(b,'project_id');value=b.get('preliminary_score');active=bool(b.get('active',True))
                    p=db.execute('SELECT category,active FROM projects WHERE id=?',(pid,)).fetchone()
                    if not p:raise ApiError('项目不存在',404)
                    maximum=sum(d['max'] for d in event_categories(db)[p['category']]['dimensions'])
                    if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=maximum or Decimal(str(value))*100!=(Decimal(str(value))*100).to_integral_value()): raise ApiError(f'初试平均分应为0～{maximum}，最多保留两位小数')
                    source=b.get('initial_source','manual' if value is not None else 'system')
                    if source not in ('manual','system'):raise ApiError('初试分数来源无效')
                    if active and ((value is None and source=='manual') or not p['active']): raise ApiError('请先填写初试平均分并启用项目，或选择评委平均分')
                    if active:enable_final_category(db,p['category'])
                    db.execute('INSERT INTO finalists VALUES (?,?,?) ON CONFLICT(project_id) DO UPDATE SET preliminary_score=excluded.preliminary_score,active=excluded.active',(pid,value,int(active)))
                    db.execute('UPDATE ranking_projects SET initial_manual=?,initial_source=? WHERE project_id=?',(value,source,pid))
                    if source=='system' and not db.execute('SELECT 1 FROM ranking_projects WHERE project_id=?',(pid,)).fetchone():
                        config=next(r for r in ranking_projects(db) if r['project_id']==pid)
                        db.execute('INSERT INTO ranking_projects VALUES (?,?,?,?,?,?,?,?)',(pid,config['included'],'system',value,'system',None,'computed',None))
                    if not active:clear_presentation(db,pid,'final')
                    data={'ok':True}
                elif path=='/api/admin/round' and method=='POST':
                    round_name=json_body(env).get('round');score_tables(round_name)
                    if round_name=='final' and (not final_enabled(db) or not review_projects(db,'final')): raise ApiError('请先勾选至少一个项目加入复试名单')
                    db.execute("INSERT INTO settings VALUES ('scoring_round',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(round_name,))
                    data={'ok':True,'round':round_name}
                elif path=='/api/admin/logout' and method=='POST':
                    c=SimpleCookie(); c.load(env.get('HTTP_COOKIE',''))
                    with connect(admin=True) as shared:shared.execute('DELETE FROM sessions WHERE token=?',(hashlib.sha256(c['admin_session'].value.encode()).hexdigest(),))
                    headers.append(('Set-Cookie','admin_session=; HttpOnly; SameSite=Strict; Path=/api/admin; Max-Age=0'))
                    data={'ok':True}
                elif path=='/api/admin/judges' and method=='POST':
                    b=json_body(env); jid=b.get('id') or uuid.uuid4().hex
                    name=required_text(b,'name',50); active=1 if b.get('active',True) else 0
                    userid=b.get('wecom_userid');corp=wecom_auth.configuration()['corp']
                    if userid is not None:
                        if not corp:raise ApiError('请先在服务器配置企业微信CorpID')
                        if not isinstance(userid,str) or len(userid)>128 or any(c.isspace() or ord(c)<32 for c in userid):raise ApiError('企业微信UserID格式无效')
                        existing=db.execute('SELECT judge_id FROM wecom_bindings WHERE corp_id=? AND userid=?',(corp,userid)).fetchone() if userid else None
                        if existing and existing['judge_id']!=jid:raise ApiError('该企业微信账号已绑定其他评委')
                    db.execute('INSERT INTO judges VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,active=excluded.active',(jid,name,active))
                    if userid is not None:
                        db.execute('DELETE FROM wecom_bindings WHERE corp_id=? AND judge_id=?',(corp,jid))
                        if userid:db.execute('INSERT INTO wecom_bindings VALUES (?,?,?)',(corp,userid,jid))
                    if not active:
                        db.execute('UPDATE judge_access SET enabled=0 WHERE judge_id=?',(jid,))
                        db.execute('DELETE FROM judge_sessions WHERE judge_id=?',(jid,))
                    data={'id':jid}
                elif path=='/api/admin/entry-url' and method=='POST':
                    url=public_entry_url(json_body(env).get('url'))
                    db.execute("INSERT INTO settings(key,value) VALUES ('public_base_url',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(url,))
                    data={'url':url}
                elif path=='/api/admin/judge-entry' and method=='POST':
                    b=json_body(env);jid=required_text(b,'judge_id');action=b.get('action','get')
                    if action not in ('get','reset','disable'): raise ApiError('入口操作无效')
                    judge=db.execute('SELECT id,name,active FROM judges WHERE id=?',(jid,)).fetchone()
                    if not judge: raise ApiError('评委不存在',404)
                    if not judge['active']: raise ApiError('请先启用该评委')
                    access=db.execute('SELECT * FROM judge_access WHERE judge_id=?',(jid,)).fetchone()
                    if action=='disable':
                        db.execute('UPDATE judge_access SET enabled=0 WHERE judge_id=?',(jid,))
                        db.execute('DELETE FROM judge_sessions WHERE judge_id=?',(jid,))
                        data={'ok':True}
                    else:
                        if not access or not access['enabled'] or action=='reset':
                            db.execute('INSERT INTO judge_access VALUES (?,?,1) ON CONFLICT(judge_id) DO UPDATE SET nonce=excluded.nonce,enabled=1',(jid,secrets.token_urlsafe(24)))
                            db.execute('DELETE FROM judge_sessions WHERE judge_id=?',(jid,))
                            access=db.execute('SELECT * FROM judge_access WHERE judge_id=?',(jid,)).fetchone()
                        data={'judge_id':jid,'name':judge['name'],'token':entry_token(db,access)}
                elif path=='/api/admin/projects' and method=='POST':
                    b=json_body(env); pid=b.get('id') or uuid.uuid4().hex
                    old=db.execute('SELECT category,intro FROM projects WHERE id=?',(pid,)).fetchone()
                    cat=b.get('category'); num=b.get('number')
                    if cat not in event_categories(db) or type(num) is not int or num<1 or num>99999: raise ApiError('分类或序号无效')
                    if old and old['category']!=cat and (db.execute('SELECT 1 FROM scores WHERE project_id=? UNION ALL SELECT 1 FROM final_scores WHERE project_id=?',(pid,pid)).fetchone() or db.execute('SELECT 1 FROM finalists WHERE project_id=?',(pid,)).fetchone()): raise ApiError('该项目已有评分或已配置复试，不能更换分类，请新增项目')
                    intro=b.get('intro',old['intro'] if old else '')
                    if not isinstance(intro,str) or len(intro)>10000: raise ApiError('项目介绍不能超过 10000 字')
                    args=(pid,cat,num,required_text(b,'title'),required_text(b,'person',100),required_text(b,'department',100),intro,1 if b.get('active',True) else 0)
                    db.execute('INSERT INTO projects VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET category=excluded.category,number=excluded.number,title=excluded.title,person=excluded.person,department=excluded.department,intro=excluded.intro,active=excluded.active',args)
                    if not args[-1]:clear_presentation(db,pid)
                    data={'id':pid}
                elif path=='/api/admin/project-template' and method=='GET':
                    category=parse_qs(env.get('QUERY_STRING','')).get('category',[''])[0]
                    if category not in event_categories(db) or (scoring_round(db)=='final' and category not in final_categories(db)):raise ApiError('请选择当前阶段的项目模块')
                    template=ROOT/'static'/'templates'/f'projects-{category}.xlsx'
                    if template.is_file() and event_categories(db)[category]['name']==CATEGORIES.get(category,{}).get('name'):data=template.read_bytes()
                    else:
                        source=(ROOT/'static/templates/projects-agent.xlsx').read_bytes();output=io.BytesIO()
                        name=event_categories(db)[category]['name']
                        sheet_name=re.sub(r'[\\/*?:\[\]]','_',name).strip("'")[:31] or '项目导入'
                        with zipfile.ZipFile(io.BytesIO(source)) as original,zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as target:
                            for entry in original.infolist():
                                content=original.read(entry.filename)
                                if entry.filename.endswith('.xml'):
                                    content=content.decode('utf-8').replace('智能体设计',escape(sheet_name,{'"':'&quot;'})).encode('utf-8')
                                target.writestr(entry.filename,content)
                        data=output.getvalue()
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                    headers.append(('Content-Disposition',f'attachment; filename="projects-{category}.xlsx"'))
                elif path=='/api/admin/projects-import' and method=='POST':
                    from project_import import parse_projects,validate_projects,ProjectImportError
                    b=json_body(env);category=b.get('category');mode=b.get('mode')
                    if category not in event_categories(db) or (scoring_round(db)=='final' and category not in final_categories(db)):raise ApiError('请选择当前阶段的项目模块')
                    try:
                        if mode=='preview':
                            try:raw=base64.b64decode(b.get('data',''),validate=True)
                            except Exception:raise ApiError('导入文件内容无效')
                            rows=parse_projects(raw,required_text(b,'filename',250),category,{c['name']:c['id'] for c in event_rubrics(db)})
                        elif mode=='import':
                            db.execute('BEGIN IMMEDIATE');rows=b.get('rows')
                        else:raise ApiError('导入操作无效')
                        if scoring_round(db)=='final' and category not in final_categories(db):raise ApiError('该分类未启用复试')
                        rows=validate_projects(db,category,rows)
                        if mode=='preview':data={'category':category,'rows':rows}
                        else:
                            for row in rows:db.execute('INSERT INTO projects(id,category,number,title,person,department,intro) VALUES (?,?,?,?,?,?,?)',(uuid.uuid4().hex,category,row['number'],row['title'],row['person'],row['department'],''))
                            data={'imported':len(rows),'category':category}
                    except ProjectImportError as error:raise ApiError(str(error))
                elif path=='/api/admin/subitem-rules' and method=='POST':
                    b=json_body(env); c=b.get('category'); k=b.get('dimension'); sub=b.get('subdimension')
                    if c not in event_categories(db) or type(k) is not int or not 0<=k<len(event_categories(db)[c]['dimensions']): raise ApiError('评分项无效')
                    if type(sub) is not int or not 0<=sub<len(event_categories(db)[c]['dimensions'][k].get('subitems',[])): raise ApiError('子评分项无效')
                    elements=b.get('elements')
                    if not isinstance(elements,list) or len(elements)!=5 or any(not isinstance(e,str) or not e.strip() or len(e)>300 for e in elements): raise ApiError('请填写5条核对要素，每条不超过300字')
                    rule={'name':required_text(b,'name',50),'definition':required_text(b,'definition',1000),'elements':[e.strip() for e in elements]}
                    saved=db.execute("SELECT value FROM settings WHERE key='subitem_rules'").fetchone()
                    rules=json.loads(saved['value']) if saved else {};rules[f'{c}:{k}:{sub}']=rule
                    db.execute("INSERT INTO settings(key,value) VALUES ('subitem_rules',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(rules,ensure_ascii=False),))
                    data={'ok':True}
                elif path=='/api/admin/score-bands' and method=='POST':
                    b=json_body(env); category=b.get('category'); index=b.get('dimension'); bands=b.get('bands')
                    if category not in event_categories(db) or type(index) is not int or not 0<=index<len(event_categories(db)[category]['dimensions']): raise ApiError('评分项无效')
                    if not isinstance(bands,list) or not 1<=len(bands)<=10: raise ApiError('请设置 1～10 个评分区间')
                    limit=event_categories(db)[category]['dimensions'][index]['max']; next_min=0; cleaned=[]
                    for band in bands:
                        if not isinstance(band,dict): raise ApiError('评分区间无效')
                        lo=band.get('min'); hi=band.get('max')
                        if type(lo) is not int or type(hi) is not int or lo!=next_min or hi<lo or hi>limit: raise ApiError(f'区间应连续覆盖 0～{limit} 分，不得重复或留空')
                        cleaned.append({'min':lo,'max':hi,'label':required_text(band,'label',30),'description':required_text(band,'description',500)})
                        next_min=hi+1
                    if next_min!=limit+1: raise ApiError(f'区间应连续覆盖 0～{limit} 分')
                    saved=db.execute("SELECT value FROM settings WHERE key='score_bands'").fetchone()
                    overrides=json.loads(saved['value']) if saved else {}
                    overrides[f'{category}:{index}']=cleaned
                    db.execute("INSERT INTO settings(key,value) VALUES ('score_bands',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(overrides,ensure_ascii=False),))
                    data={'ok':True}
                elif path=='/api/admin/settings' and method=='POST':
                    b=json_body(env)
                    for k in ('title','subtitle'):
                        val=required_text(b,k,100)
                        db.execute('UPDATE settings SET value=? WHERE key=?',(val,k))
                    if b.get('title'):competition.rename(b['title'].strip())
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
                elif path=='/api/admin/ranking-export' and method=='GET':
                    from excel_export import build_ranking_report
                    data=build_ranking_report(leaderboard(db,True),ranking_settings(db));mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                    headers.append(('Content-Disposition','attachment; filename="competition-rankings.xlsx"'))
                elif path=='/api/admin/export' and method=='GET':
                    data=excel_bytes(db); mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                    headers.append(('Content-Disposition','attachment; filename="AI-competition-scores.xlsx"'))
                else: raise ApiError('接口不存在',404)
            elif method in ('GET','HEAD'):
                if re.fullmatch(r'/WW_verify_[A-Za-z0-9_-]{1,100}\.txt',path):
                    file=DATA/'wecom-verification'/path.lstrip('/')
                    if not file.is_file():file=ROOT/'static/wecom-verification'/path.lstrip('/')
                elif path in ('/','/admin','/admin/','/leaderboard','/leaderboard/'):
                    file=ROOT/'static'/('admin.html' if path.startswith('/admin') else 'leaderboard.html' if path.startswith('/leaderboard') else 'index.html')
                elif path.startswith('/static/'):
                    file=(ROOT/path.lstrip('/')).resolve()
                    if ROOT/'static' not in file.parents: raise ApiError('未找到页面',404)
                elif path.startswith('/media/'):
                    file=(DATA/'media'/path.split('/')[-1]).resolve()
                    if DATA/'media' not in file.parents: raise ApiError('文件不存在',404)
                else: raise ApiError('未找到页面',404)
                if not file.is_file(): raise ApiError('文件不存在',404)
                data=file.read_bytes(); mime={'.webp':'image/webp','.avif':'image/avif'}.get(file.suffix.lower()) or mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
                if mime.startswith('text/') or mime in ('application/javascript',): mime+='; charset=utf-8'
            else: raise ApiError('请求方法不支持',405)
        if not isinstance(data,bytes): data=json.dumps(data,ensure_ascii=False,allow_nan=False).encode('utf-8')
    except ApiError as e:
        status=e.status; data=json.dumps({'error':e.message,**({'code':e.code} if e.code else {})},ensure_ascii=False).encode()
    except Exception:
        import traceback
        traceback.print_exc()
        status=500; data=json.dumps({'error':'服务暂时不可用，请稍后重试'},ensure_ascii=False).encode()
    phrase={200:'OK',302:'Found',400:'Bad Request',401:'Unauthorized',403:'Forbidden',404:'Not Found',405:'Method Not Allowed',409:'Conflict',413:'Payload Too Large',429:'Too Many Requests',500:'Internal Server Error'}[status]
    cache='public, max-age=31536000, immutable' if status==200 and path.startswith('/static/competition-poster-v1-') and path.endswith(('.webp','.avif')) else 'no-store'
    headers += [('Content-Type',mime),('Content-Length',str(len(data))),('Cache-Control',cache),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Referrer-Policy','same-origin')]
    start_response(f'{status} {phrase}',headers)
    return [b'' if method=='HEAD' else data]


init_db()
with connect(admin=True) as initial:
    competition.initialize(DATA,dict(initial.execute('SELECT key,value FROM settings')).get('title','已有比赛'))
for existing in competition.listing():
    if existing['id']=='default':continue
    token=competition.current.set(existing['id'])
    try:init_db()
    finally:competition.current.reset(token)


def application(env,start_response):
    def dispatch():
        token=competition.current.set(competition.active())
        try:return _application(env,start_response)
        finally:competition.current.reset(token)
    if env.get('REQUEST_METHOD') in ('POST','PUT','DELETE'):
        with competition.mutation():return dispatch()
    return dispatch()

if __name__=='__main__':
    class SafeRequestHandler(WSGIRequestHandler):
        def log_request(self,code='-',size='-'):
            self.log_message('"%s %s" %s %s',self.command,self.path.split('?',1)[0],code,size)
    class ThreadedServer(ThreadingMixIn,WSGIServer):
        daemon_threads=True
    host=os.environ.get('HOST','127.0.0.1'); port=int(os.environ.get('PORT','8000'))
    print(f'Local: http://{host}:{port}',flush=True)
    print(f'Admin: http://{host}:{port}/admin',flush=True)
    if not os.environ.get('ADMIN_PASSWORD'): print(f'Temporary admin password: {ADMIN_PASSWORD}',flush=True)
    with make_server(host,port,application,server_class=ThreadedServer,handler_class=SafeRequestHandler) as server:
        server.serve_forever()
