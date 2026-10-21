"""Enterprise WeChat OAuth client. Credentials never leave the server."""
import json
import os
import threading
import time
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


class WecomError(Exception):
    pass


def configuration():
    corp=os.environ.get('WECOM_CORP_ID','').strip()
    agent=os.environ.get('WECOM_AGENT_ID','').strip()
    secret=os.environ.get('WECOM_SECRET','').strip()
    base=os.environ.get('WECOM_PUBLIC_URL','').strip().rstrip('/')
    parsed=urlparse(base)
    valid=bool(parsed.scheme in ('http','https') and parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and parsed.path in ('','/'))
    return {'corp':corp,'agent':agent,'secret':secret,'base':base,'ready':bool(corp and agent.isdigit() and secret and valid)}


def authorization_url(state):
    config=configuration()
    if not config['ready']:raise WecomError('not_configured')
    return 'https://open.weixin.qq.com/connect/oauth2/authorize?'+urlencode({
        'appid':config['corp'],'redirect_uri':config['base']+'/api/wecom/callback',
        'response_type':'code','scope':'snsapi_base','state':state,'agentid':config['agent']})+'#wechat_redirect'


def _request(endpoint,params):
    # Fixed API host and TLS verification; no proxy/URL supplied by the browser.
    try:
        request=Request('https://qyapi.weixin.qq.com/cgi-bin/'+endpoint+'?'+urlencode(params),headers={'Accept':'application/json'})
        with urlopen(request,timeout=8) as response:
            raw=response.read(65537)
        if len(raw)>65536:raise ValueError()
        value=json.loads(raw)
        if not isinstance(value,dict):raise ValueError()
        return value
    except Exception:
        # Upstream exception URLs may contain application secrets or access tokens.
        raise WecomError('network') from None


_lock=threading.Lock()
_cache={}


def access_token(config,force=False):
    key=(config['corp'],config['secret'])
    with _lock:
        if not force and _cache.get('key')==key and _cache.get('until',0)>time.time():return _cache['token']
        result=_request('gettoken',{'corpid':config['corp'],'corpsecret':config['secret']})
        if result.get('errcode',0) or not isinstance(result.get('access_token'),str):raise WecomError('token')
        try:ttl=max(0,min(int(result.get('expires_in',7200))-120,7200))
        except (ValueError,TypeError):ttl=600
        _cache.update(key=key,token=result['access_token'],until=time.time()+ttl)
        return result['access_token']


def identify(code):
    config=configuration()
    if not config['ready']:raise WecomError('not_configured')
    for attempt in range(2):
        token=access_token(config,force=bool(attempt))
        result=_request('auth/getuserinfo',{'access_token':token,'code':code})
        if result.get('errcode') in (40014,42001) and not attempt:continue
        if result.get('errcode',0):raise WecomError('identity')
        userid=result.get('userid') or result.get('UserId')
        if not isinstance(userid,str) or not userid.strip() or len(userid)>128:raise WecomError('not_member')
        return config['corp'],userid.strip()
    raise WecomError('identity')
