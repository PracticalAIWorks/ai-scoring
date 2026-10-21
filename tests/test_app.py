import io
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlparse,parse_qs
import hashlib
import time
import subprocess
import sys
from contextlib import closing
import xml.etree.ElementTree as ET
import zipfile

TEMP = tempfile.TemporaryDirectory()
os.environ['DATA_DIR'] = TEMP.name
os.environ['ADMIN_PASSWORD'] = 'test-admin-password'
import app


class ScoringTest(unittest.TestCase):
    def setUp(self):
        app.competition.activate('default')
        with app.connect() as db:
            db.execute('DELETE FROM ranking_projects')
            db.execute("DELETE FROM settings WHERE key IN ('leaderboard_categories','leaderboard_mode','leaderboard_scroll_speed','leaderboard_scroll_enabled','leaderboard_scroll_top_hold','leaderboard_scroll_bottom_hold')")
            db.execute('DELETE FROM final_subscores')
            db.execute('DELETE FROM final_scores')
            db.execute('DELETE FROM final_write_versions')
            db.execute('DELETE FROM finalists')
            db.execute('DELETE FROM subscores')
            db.execute('DELETE FROM scores')
            db.execute('DELETE FROM write_versions')
            db.execute('DELETE FROM sessions')
            db.execute('DELETE FROM judge_sessions')
            db.execute('DELETE FROM general_sessions')
            db.execute('DELETE FROM wecom_states')
            db.execute('DELETE FROM wecom_sessions')
            db.execute('DELETE FROM wecom_bindings')
            db.execute('DELETE FROM judge_access')
            db.execute("DELETE FROM projects WHERE id NOT LIKE 'demo-project-%'")
            db.execute("DELETE FROM settings WHERE key IN ('score_bands','subitem_rules','public_base_url','scoring_round','presenting_preliminary','presenting_final','entry_mode','event_rubrics','rules_version','final_enabled','preliminary_weight','final_weight','show_intro')")
            db.execute('UPDATE judges SET active=1')
            for i in range(1,4):
                db.execute('UPDATE judges SET name=? WHERE id=?',(f'演示评委{i}',f'demo-judge-{i}'))
            db.execute('UPDATE projects SET active=1')
        self.cookie = ''
        self.judge_cookies={};self.entry_tokens={}
        self.login()
        for judge in ['demo-judge-1','demo-judge-2','demo-judge-3']:
            r=self.call('/api/admin/judge-entry','POST',{'judge_id':judge},admin=True)
            self.assertEqual(r['status'],200)
            self.entry_tokens[judge]=r['json']['token']
            r=self.call('/api/judge/enter','POST',{'token':r['json']['token']},judge_as='')
            self.assertEqual(r['status'],200)
            self.judge_cookies[judge]=r['headers']['Set-Cookie'].split(';')[0]

    def call(self,path,method='GET',body=None,admin=False,origin=None,judge_as='demo-judge-1',cookie=None):
        raw=json.dumps(body or {}).encode()
        url,_,query=path.partition('?')
        env={'PATH_INFO':url,'QUERY_STRING':query,'REQUEST_METHOD':method,'CONTENT_LENGTH':str(len(raw)),
             'wsgi.input':io.BytesIO(raw),'HTTP_HOST':'localhost:8000','REMOTE_ADDR':'127.0.0.1'}
        if admin:env['HTTP_COOKIE']=self.cookie
        elif self.judge_cookies.get(judge_as):env['HTTP_COOKIE']=self.judge_cookies[judge_as]
        if cookie is not None:env['HTTP_COOKIE']=cookie
        if origin:env['HTTP_ORIGIN']=origin
        response={}
        def start(status,headers):response.update(status=int(status.split()[0]),headers=dict(headers),all_headers=headers)
        data=b''.join(app.application(env,start))
        response['raw']=data
        if response['headers']['Content-Type'].startswith('application/json'):response['json']=json.loads(data)
        return response

    def login(self):
        r=self.call('/api/admin/login','POST',{'password':'test-admin-password'})
        self.assertEqual(r['status'],200)
        self.cookie=r['headers']['Set-Cookie'].split(';')[0]

    def score(self,k,v,judge='demo-judge-1',project='demo-project-1'):
        return self.call('/api/score','PUT',{'judge_id':judge,'project_id':project,'dimension':k,'value':v},judge_as=judge)

    def only_judges(self,*ids):
        with app.connect() as db:
            db.execute("UPDATE judges SET active=0")
            for jid in ids:db.execute("UPDATE judges SET active=1 WHERE id=?",(jid,))

    def ranking_options(self,**overrides):
        return self.call('/api/admin/ranking-settings','POST',{'categories':['prompt','solution','agent'],'mode':'weighted','preliminary_weight':70,'final_weight':30,**overrides},admin=True)

    def ranking_project(self,pid='demo-project-1',**overrides):
        return self.call('/api/admin/ranking-project','POST',{'project_id':pid,'included':True,'initial_source':'manual','initial_manual':80,'final_source':'manual','final_manual':90,'result_source':'computed','result_manual':None,**overrides},admin=True)

    def ranking_row(self,pid='demo-project-1'):
        return next(p for c in self.call('/api/leaderboard')['json']['categories'] for p in c['projects'] if p['id']==pid)

    def project_batch(self,action,category='prompt',ids=None,**extra):
        return self.call('/api/admin/project-batch','POST',{'action':action,'category':category,'project_ids':ids if ids is not None else ['demo-project-1','demo-project-2'],**extra},admin=True)

    def test_batch_prompt_finalists_auto_enables_module_and_system_sources(self):
        self.score(0,23)
        initial=self.call('/api/admin/state',admin=True)['json']
        self.assertNotIn('prompt',initial['final_categories'])
        self.assertTrue(all(r['initial_source']=='system' and r['final_source']=='system' for r in initial['ranking_projects']))
        self.assertEqual(self.project_batch('finalists_add')['status'],200)
        current=self.call('/api/admin/state',admin=True)['json']
        self.assertIn('prompt',current['final_categories']);self.assertEqual(current['round'],'preliminary')
        self.assertEqual({r['project_id'] for r in current['finalists'] if r['active']},{'demo-project-1','demo-project-2'})
        for config in current['ranking_projects']:
            if config['project_id'] in ('demo-project-1','demo-project-2'):
                self.assertEqual((config['initial_source'],config['final_source'],config['result_source'],config['included']),('system','system','computed',1))
        self.assertEqual(self.ranking_row()['score'],None)
        self.assertEqual(self.switch_round()['status'],200)
        bootstrap=self.call('/api/bootstrap')['json']
        self.assertTrue({'demo-project-1','demo-project-2'}.issubset({p['id'] for p in bootstrap['projects']}))
        self.assertEqual(self.final_score(0,20,project='demo-project-1')['status'],200)
        with app.connect() as db:
            self.assertEqual(db.execute("SELECT value FROM scores WHERE project_id='demo-project-1'").fetchone()[0],23)
        self.assertEqual(self.project_batch('finalists_add')['json']['count'],2)  # Repeated selection is idempotent.

    def test_batch_ranking_independent_qualification_and_manual_values_retained(self):
        self.ranking_project(initial_manual=81,final_manual=92,result_source='manual',result_manual=96)
        self.score(0,17)
        self.assertEqual(self.project_batch('ranking_add')['status'],200)
        state=self.call('/api/admin/state',admin=True)['json']
        self.assertFalse(state['finalists']);self.assertNotIn('prompt',state['final_categories'])
        config=next(c for c in state['ranking_projects'] if c['project_id']=='demo-project-1')
        self.assertEqual((config['initial_source'],config['final_source'],config['result_source']),('system','system','computed'))
        self.assertEqual((config['initial_manual'],config['final_manual'],config['result_manual']),(81,92,96))
        self.assertIn('prompt',state['ranking_settings']['categories'])
        self.project_batch('finalists_add',include_ranking=False)
        self.project_batch('ranking_remove')
        state=self.call('/api/admin/state',admin=True)['json']
        self.assertTrue(all(f['active'] for f in state['finalists']))
        self.assertFalse(any(c['included'] for c in state['ranking_projects'] if c['project_id'] in ['demo-project-1','demo-project-2']))
        self.project_batch('ranking_add')
        self.project_batch('finalists_remove')
        state=self.call('/api/admin/state',admin=True)['json']
        self.assertFalse(any(f['active'] for f in state['finalists']))
        self.assertTrue(all(c['included'] for c in state['ranking_projects'] if c['project_id'] in ['demo-project-1','demo-project-2']))
        with app.connect() as db:self.assertEqual(db.execute('SELECT value FROM scores').fetchone()[0],17)

    def test_batch_atomic_validation_and_permissions(self):
        for extra in [{'ids':[]},{'ids':['demo-project-1','demo-project-1']},{'ids':['demo-project-1','missing']},{'ids':['demo-project-1','demo-project-3']},{'ids':[True]},{'category':'missing'},{'include_ranking':'yes'},{'use_system_scores':1}]:
            self.assertEqual(self.project_batch('finalists_add',**extra)['status'],400)
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM finalists').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ranking_projects').fetchone()[0],0)
            self.assertNotIn('prompt',app.final_categories(db))
            db.execute("UPDATE projects SET active=0 WHERE id='demo-project-2'")
        self.assertEqual(self.project_batch('finalists_add')['status'],400)
        with app.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM finalists').fetchone()[0],0)
        self.assertEqual(self.call('/api/admin/project-batch','POST',{'action':'finalists_add','category':'prompt','project_ids':['demo-project-1']})['status'],401)
        identifier=self.new_competition()
        self.assertEqual(self.project_batch('finalists_add')['status'],409)

    def test_single_finalist_uses_automatic_scores_without_manual_initial(self):
        self.assertEqual(self.call('/api/admin/finalists','POST',{'project_id':'demo-project-1'},admin=True)['status'],200)
        state=self.call('/api/admin/state',admin=True)['json']
        self.assertIn('prompt',state['final_categories'])
        config=next(c for c in state['ranking_projects'] if c['project_id']=='demo-project-1')
        self.assertEqual((config['initial_source'],config['final_source']),('system','system'))
        self.assertIsNone(config['initial_manual'])

    def test_batch_sources_reset_and_single_stage_activation(self):
        self.ranking_project(initial_manual=70,final_manual=90,result_source='manual',result_manual=95)
        self.assertEqual(self.project_batch('use_system_scores',ids=['demo-project-1'])['status'],200)
        with app.connect() as db:
            config=dict(db.execute("SELECT * FROM ranking_projects WHERE project_id='demo-project-1'").fetchone())
            self.assertEqual((config['initial_manual'],config['final_manual'],config['result_manual']),(70,90,95))
            self.assertEqual((config['initial_source'],config['final_source'],config['result_source']),('system','system','computed'))
            app.setting(db,'final_enabled','0')
        self.assertEqual(self.project_batch('finalists_add',include_ranking=False)['status'],200)
        state=self.call('/api/admin/state',admin=True)['json']
        self.assertEqual(state['settings']['final_enabled'],'1')
        self.assertEqual(state['round'],'preliminary')

    def test_batch_automatic_initial_score_is_used_in_final_scoring_export(self):
        self.configure_final('demo-project-1',81)
        self.project_batch('finalists_add',ids=['demo-project-1'])
        with app.connect() as db:
            for jid in ['demo-judge-1','demo-judge-2','demo-judge-3']:
                for k,value in enumerate([25,25,20,20,10]):db.execute('INSERT INTO scores VALUES (?,?,?,?,?)',(jid,'demo-project-1',k,value,app.now()))
                for k,value in enumerate([20,20,15,15,10]):db.execute('INSERT INTO final_scores VALUES (?,?,?,?,?)',(jid,'demo-project-1',k,value,app.now()))
            self.assertEqual(db.execute("SELECT preliminary_score FROM finalists WHERE project_id='demo-project-1'").fetchone()[0],81)
        self.switch_round()
        self.assertEqual(self.ranking_row()['score'],94)
        with zipfile.ZipFile(io.BytesIO(self.call('/api/admin/export',admin=True)['raw'])) as archive:
            xml=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'));ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            cells={cell.attrib['r']:cell.find('s:v',ns).text for cell in xml.findall('.//s:c',ns) if cell.find('s:v',ns) is not None}
            self.assertEqual((cells['I4'],cells['J4'],cells['K4']),('100.0','80.0','94.0'))

    def test_ranking_any_module_select_hide_and_independent_final_eligibility(self):
        self.assertEqual(self.ranking_options(categories=['prompt'])['status'],200)
        self.assertEqual(self.ranking_project()['status'],200)
        board=self.call('/api/leaderboard')['json']
        self.assertEqual([c['id'] for c in board['categories']],['prompt'])
        self.assertEqual(self.ranking_row()['score'],83)
        self.assertEqual(set(self.ranking_row()),{'id','title','person','department','score','rank'})
        self.assertEqual(self.call('/api/admin/state',admin=True)['json']['finalists'],[])
        self.ranking_options(categories=[])
        self.assertEqual(self.call('/api/leaderboard')['json']['categories'],[])
        self.ranking_options(categories=['prompt'])
        self.assertEqual(self.ranking_row()['score'],83)
        self.ranking_project(included=False)
        self.assertEqual(self.call('/api/leaderboard')['json']['categories'][0]['projects'],[])

    def test_ranking_stage_modes_manual_override_zero_and_pending(self):
        self.ranking_options();self.ranking_project()
        for mode,want in [('preliminary',80),('final',90),('weighted',83)]:
            self.ranking_options(mode=mode);self.assertEqual(self.ranking_row()['score'],want)
        self.ranking_project(result_source='manual',result_manual=0)
        self.assertEqual(self.ranking_row()['score'],0)
        self.ranking_project(result_source='manual',result_manual=None)
        self.assertIsNone(self.ranking_row()['score'])
        self.ranking_project(result_source='computed',result_manual=98)
        self.assertEqual(self.ranking_row()['score'],83)
        self.ranking_options(mode='manual');self.assertEqual(self.ranking_row()['score'],98)
        self.ranking_options(mode='weighted',preliminary_weight=100,final_weight=0)
        self.ranking_project(final_manual=None);self.assertEqual(self.ranking_row()['score'],80)
        self.ranking_options(preliminary_weight=0,final_weight=100)
        self.ranking_project(initial_manual=None);self.assertEqual(self.ranking_row()['score'],90)

    def test_ranking_automatic_scores_live_and_original_records_preserved(self):
        self.only_judges("demo-judge-1")
        self.ranking_options();self.ranking_project(initial_source='system',final_source='system')
        self.assertIsNone(self.ranking_row()['score'])
        with app.connect() as db:
            for k,value in enumerate([20,20,15,15,10]):db.execute('INSERT INTO scores VALUES (?,?,?,?,?)',('demo-judge-1','demo-project-1',k,value,app.now()))
            for k,value in enumerate([25,25,20,20,10]):db.execute('INSERT INTO final_scores VALUES (?,?,?,?,?)',('demo-judge-1','demo-project-1',k,value,app.now()))
            db.execute('INSERT INTO final_scores VALUES (?,?,?,?,?)',('demo-judge-2','demo-project-1',0,1,app.now()))
        self.assertEqual(self.ranking_row()['score'],86)
        self.score(0,24);self.assertEqual(self.ranking_row()['score'],88.8)
        self.ranking_project(initial_manual=10,final_manual=20)
        self.assertEqual(self.ranking_row()['score'],13)
        self.ranking_project(initial_source='system',final_source='system')
        self.assertEqual(self.ranking_row()['score'],88.8)
        with app.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM scores').fetchone()[0],5)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM final_scores').fetchone()[0],6)
            db.execute("UPDATE judges SET active=0 WHERE id='demo-judge-1'")
        self.assertIsNone(self.ranking_row()['score'])  # No active panel cannot release a score.
        with app.connect() as db:db.execute("UPDATE projects SET active=0 WHERE id='demo-project-1'")
        self.assertFalse(any(p['id']=='demo-project-1' for c in self.call('/api/leaderboard')['json']['categories'] for p in c['projects']))

    def test_ranking_validation_authorization_and_precise_rounding(self):
        for body in [{'categories':['unknown']},{'categories':['prompt','prompt']},{'categories':'prompt'},{'mode':'bad'},{'preliminary_weight':70.5},{'final_weight':-1},{'preliminary_weight':True}]:
            self.assertEqual(self.ranking_options(**body)['status'],400)
        for key in ['initial_manual','final_manual','result_manual']:
            for value in [-1,101,True,'80',80.123,float('nan'),float('inf')]:self.assertEqual(self.ranking_project(**{key:value})['status'],400)
        for body in [{'initial_source':'other'},{'final_source':'other'},{'result_source':'other'},{'included':'yes'}]:self.assertEqual(self.ranking_project(**body)['status'],400)
        self.assertEqual(self.call('/api/admin/ranking-settings','POST',{},admin=False)['status'],401)
        self.assertEqual(self.call('/api/admin/ranking-project','POST',{},admin=False)['status'],401)
        self.ranking_options(preliminary_weight=50,final_weight=50)
        self.ranking_project(initial_manual=80.01,final_manual=80)
        self.assertEqual(self.ranking_row()['score'],80.01)

    def test_ranking_requires_every_active_judge_in_each_automatic_stage(self):
        self.ranking_options();self.ranking_project(initial_source='system',final_source='system')
        def complete(table,jid):
            with app.connect() as db:
                for k,value in enumerate([20,20,15,15,10]):db.execute(f'INSERT OR REPLACE INTO {table} VALUES (?,?,?,?,?)',(jid,'demo-project-1',k,value,app.now()))
        for jid in ['demo-judge-1','demo-judge-2']:
            complete('scores',jid);complete('final_scores',jid)
            self.assertIsNone(self.ranking_row()['score']);self.assertIsNone(self.ranking_row()['rank'])
        complete('scores','demo-judge-3')
        self.assertIsNone(self.ranking_row()['score'])  # Final stage still lacks the third judge.
        complete('final_scores','demo-judge-3')
        self.assertEqual(self.ranking_row()['score'],80)
        with app.connect() as db:db.execute("DELETE FROM final_scores WHERE judge_id='demo-judge-3' AND dimension=4")
        self.assertIsNone(self.ranking_row()['score'])
        with app.connect() as db:db.execute("UPDATE judges SET active=0 WHERE id='demo-judge-3'")
        self.assertEqual(self.ranking_row()['score'],80)
        with app.connect() as db:db.execute("UPDATE judges SET active=1 WHERE id='demo-judge-3'")
        self.assertIsNone(self.ranking_row()['score'])
        complete('final_scores','demo-judge-3')
        self.assertEqual(self.ranking_row()['score'],80)
        self.score(0,23)
        self.assertEqual(self.ranking_row()['score'],80.7)  # Completed panel edits update normally.
        with zipfile.ZipFile(io.BytesIO(self.call('/api/admin/ranking-export',admin=True)['raw'])) as archive:
            self.assertIn('<v>80.7</v>',archive.read('xl/worksheets/sheet1.xml').decode())
        self.score(0,None)
        self.assertIsNone(self.ranking_row()['score'])
        self.ranking_options(mode='final')
        self.assertEqual(self.ranking_row()['score'],80)  # Unused initial stage does not block final-only mode.

    def managed_request(self,project='demo-project-1',stage='preliminary',judge='demo-judge-1',**changes):
        view=self.call(f'/api/admin/judge-scores?judge_id={judge}&round={stage}',admin=True)['json']
        chosen=[r for r in view['records'] if changes.get('action')=='clear_judge' or r['project_id']==project]
        body={'judge_id':judge,'round':stage,'project_id':project,'action':'save','reason':'核对更正','rules_version':view['rules_version'],'expected':{r['project_id']:{'values':r['values'],'subvalues':r['subvalues']} for r in chosen},**changes}
        return body

    def test_disabled_judge_excluded_from_both_stage_means_and_export(self):
        self.ranking_options();self.ranking_project(initial_source='system',final_source='system')
        with app.connect() as db:
            for table in ['scores','final_scores']:
                for jid in ['demo-judge-1','demo-judge-2','demo-judge-3']:
                    for k,d in enumerate(app.CATEGORIES['prompt']['dimensions']):
                        db.execute(f'INSERT INTO {table} VALUES (?,?,?,?,?)',(jid,'demo-project-1',k,0 if jid=='demo-judge-3' else d['max'],app.now()))
        self.assertEqual(self.ranking_row()['score'],66.67)
        self.call('/api/admin/judges','POST',{'id':'demo-judge-3','name':'演示评委3','active':False},admin=True)
        self.assertEqual(self.ranking_row()['score'],100)
        with app.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM scores WHERE judge_id='demo-judge-3'").fetchone()[0],5)
            data,records=app.all_records(db)
        from excel_export import build_report
        with zipfile.ZipFile(io.BytesIO(build_report(data,records,app.RUBRICS))) as archive:
            ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            tree=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            values={c.attrib['r']:c.find('m:v',ns).text for c in tree.findall('.//m:c',ns) if c.find('m:v',ns) is not None}
            self.assertEqual(float(values['I4']),100)
            self.assertEqual(float(values['J4']),2)
            self.assertEqual(float(values['H4']),0)
        self.call('/api/admin/judges','POST',{'id':'demo-judge-3','name':'演示评委3','active':True},admin=True)
        self.assertEqual(self.ranking_row()['score'],66.67)

    def test_admin_score_edit_clear_stage_and_audit(self):
        self.only_judges('demo-judge-1');self.ranking_options(mode='preliminary');self.ranking_project(initial_source='system')
        dims=app.CATEGORIES['prompt']['dimensions']
        children={str(k):{str(si):sub['max'] for si,sub in enumerate(d['subitems'])} for k,d in enumerate(dims)}
        body=self.managed_request(subvalues=children)
        self.assertEqual(self.call('/api/admin/judge-scores','POST',body,admin=True)['status'],200)
        self.assertEqual(self.ranking_row()['score'],100)
        self.assertEqual(self.call('/api/admin/judge-scores','POST',body,admin=True)['status'],409)
        final=self.managed_request(stage='final',values={str(k):0 for k in range(len(dims))})
        self.assertEqual(self.call('/api/admin/judge-scores','POST',final,admin=True)['status'],200)
        clear=self.managed_request(action='clear_project');clear.pop('reason')
        self.assertEqual(self.call('/api/admin/judge-scores','POST',clear,admin=True)['status'],200)
        self.assertIsNone(self.ranking_row()['score'])
        view=self.call('/api/admin/judge-scores?judge_id=demo-judge-1&round=final',admin=True)['json']
        self.assertTrue(next(r for r in view['records'] if r['project_id']=='demo-project-1')['complete'])
        self.assertEqual(self.call('/api/admin/judge-scores','POST',self.managed_request(stage='final',action='clear_judge',reason=''),admin=True)['status'],200)
        with app.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM final_scores WHERE judge_id='demo-judge-1'").fetchone()[0],0)
            log=db.execute('SELECT before_json,after_json FROM score_admin_log ORDER BY id DESC').fetchone()
            self.assertTrue(json.loads(log['before_json'])['values'])
            self.assertEqual(json.loads(log['after_json']),{'values':{},'subvalues':{}})

    def test_admin_score_validation_conflicts_and_unauthorized(self):
        body=self.managed_request(values={'0':5})
        self.assertEqual(self.call('/api/admin/judge-scores','POST',body)['status'],401)
        self.assertEqual(self.call('/api/admin/judge-scores?judge_id=demo-judge-1')['status'],401)
        self.assertEqual(self.call('/api/admin/judge-scores','POST',{**body,'competition_id':'missing'},admin=True)['status'],409)
        for changes in [{'round':'other'},{'judge_id':'missing'},{'reason':''},{'values':{'0':True}},{'values':{'0':1.5}},{'values':{'0':-1}},{'values':{'0':26}},{'values':{'99':0}},{'subvalues':{'0':{'99':0}}},{'values':{'0':4},'subvalues':{'0':{'0':1}}}]:
            self.assertEqual(self.call('/api/admin/judge-scores','POST',{**body,**changes},admin=True)['status'],400,changes)
        self.assertEqual(self.call('/api/admin/judge-scores','POST',{**body,'rules_version':999},admin=True)['status'],409)
        self.score(0,3)
        self.assertEqual(self.call('/api/admin/judge-scores','POST',body,admin=True)['status'],409)
        with app.connect() as db:self.assertEqual(db.execute("SELECT value FROM scores WHERE judge_id='demo-judge-1' AND project_id='demo-project-1' AND dimension=0").fetchone()[0],3)

    def test_ranking_scroll_settings_validation_public_values_and_persistence(self):
        before=self.call('/api/leaderboard')['json']['scroll_settings']
        self.assertEqual(before,{'scroll_enabled':True,'scroll_speed':16,'scroll_top_hold':12,'scroll_bottom_hold':3})
        self.assertEqual(self.ranking_options(scroll_enabled=False,scroll_speed=10,scroll_top_hold=25,scroll_bottom_hold=4)['status'],200)
        expected={'scroll_enabled':False,'scroll_speed':10,'scroll_top_hold':25,'scroll_bottom_hold':4}
        self.assertEqual(self.call('/api/leaderboard')['json']['scroll_settings'],expected)
        self.ranking_options()  # Older clients do not clear these preferences.
        app.init_db()
        self.assertEqual(self.call('/api/leaderboard')['json']['scroll_settings'],expected)
        for body in [{'scroll_speed':7},{'scroll_speed':41},{'scroll_speed':True},{'scroll_speed':12.5},{'scroll_top_hold':121},{'scroll_top_hold':-1},{'scroll_bottom_hold':31},{'scroll_enabled':'yes'}]:
            self.assertEqual(self.ranking_options(**body)['status'],400)
            self.assertEqual(self.call('/api/leaderboard')['json']['scroll_settings'],expected)
        identifier=self.new_competition()
        self.assertEqual(self.call('/api/leaderboard')['json']['scroll_settings'],before)

    def test_ranking_migration_export_and_competition_isolation(self):
        self.ranking_options(categories=['prompt']);self.ranking_project()
        app.init_db();self.assertEqual(self.ranking_row()['score'],83)
        with zipfile.ZipFile(io.BytesIO(self.call('/api/admin/ranking-export',admin=True)['raw'])) as archive:
            xml=archive.read('xl/worksheets/sheet1.xml').decode()
            for text in ['初试来源','复试来源','手动录入','83','80','90']:self.assertIn(text,xml)
        self.assertEqual(self.call('/api/admin/ranking-export',admin=False)['status'],401)
        self.ranking_options(categories=[])
        with zipfile.ZipFile(io.BytesIO(self.call('/api/admin/ranking-export',admin=True)['raw'])) as archive:self.assertIn('暂无开放',archive.read('xl/worksheets/sheet1.xml').decode())
        identifier=self.new_competition()
        self.assertEqual(self.call('/api/admin/state',admin=True)['json']['ranking_projects'],[])
        self.assertEqual(self.ranking_project()['status'],409)
        self.assertEqual(self.call('/api/admin/competitions','POST',{'action':'switch','id':'default','competition_id':identifier},admin=True)['status'],200)
        self.ranking_options(categories=['prompt']);self.assertEqual(self.ranking_row()['score'],83)

    def test_ranking_auto_initial_can_qualify_without_manual_mark(self):
        result=self.call('/api/admin/finalists','POST',{'project_id':'demo-project-3','initial_source':'system','preliminary_score':None,'active':True},admin=True)
        self.assertEqual(result['status'],200)
        self.assertEqual(self.call('/api/admin/round','POST',{'round':'final'},admin=True)['status'],200)
        self.assertIsNone(self.ranking_row('demo-project-3')['score'])

    def test_public_no_login(self):
        self.assertEqual(self.call('/')['status'],200)
        self.assertEqual(self.call('/api/bootstrap')['status'],200)

    def test_anonymous_entry_and_score_api_are_restricted(self):
        data=self.call('/api/bootstrap',judge_as='')['json']
        self.assertIsNone(data['current_judge'])
        self.assertEqual(data['judges'],[])
        self.assertEqual(data['projects'],[])
        self.assertEqual(self.call('/api/scores?judge=demo-judge-1',judge_as='')['status'],401)
        self.assertEqual(self.call('/api/score','PUT',{'judge_id':'demo-judge-1','project_id':'demo-project-1','dimension':0,'value':15},judge_as='')['status'],401)
        self.assertEqual(self.call('/api/admin/judge-entry','POST',{'judge_id':'demo-judge-1'},judge_as='')['status'],401)
        self.assertEqual(self.call('/api/admin/entry-url','POST',{'url':'https://score.example.com'},judge_as='')['status'],401)

    def test_session_identity_cannot_be_changed_by_request(self):
        data=self.call('/api/bootstrap')['json']
        self.assertEqual(data['current_judge'],{'id':'demo-judge-1','name':'演示评委1'})
        self.assertEqual(data['judges'],[data['current_judge']])
        self.assertEqual(self.call('/api/scores?judge=demo-judge-2')['status'],403)
        self.assertEqual(self.call('/api/score','PUT',{'judge_id':'demo-judge-2','project_id':'demo-project-1','dimension':0,'value':15})['status'],403)
        # Omitting an identity uses the authenticated judge, never an anonymous one.
        self.assertEqual(self.call('/api/score','PUT',{'project_id':'demo-project-1','dimension':0,'value':15})['status'],200)
        self.assertEqual(self.call('/api/scores')['json']['scores'][0]['value'],15)
        self.assertEqual(self.call('/api/scores',judge_as='demo-judge-2')['json']['scores'],[])

    def test_entry_token_stable_and_hidden_from_public_data(self):
        original=self.entry_tokens['demo-judge-1']
        app.init_db()
        current=self.call('/api/admin/judge-entry','POST',{'judge_id':'demo-judge-1'},admin=True)['json']
        self.assertEqual(original,current['token'])
        with app.connect() as db:
            signing_key=db.execute("SELECT value FROM settings WHERE key='entry_signing_key'").fetchone()['value']
            self.assertNotEqual(db.execute('SELECT token FROM judge_sessions LIMIT 1').fetchone()['token'],self.judge_cookies['demo-judge-1'].split('=',1)[1])
        for path,admin in [('/api/bootstrap',False),('/api/admin/state',True)]:
            raw=self.call(path,admin=admin)['raw'].decode()
            self.assertNotIn(original,raw)
            self.assertNotIn(signing_key,raw)
            self.assertNotIn('entry_signing_key',raw)

    def test_reset_entry_revokes_old_tokens_and_sessions_preserves_scores(self):
        self.score(0,23)
        old=self.entry_tokens['demo-judge-1']
        fresh=self.call('/api/admin/judge-entry','POST',{'judge_id':'demo-judge-1','action':'reset'},admin=True)['json']['token']
        self.assertNotEqual(old,fresh)
        self.assertEqual(self.call('/api/scores')['status'],401)
        self.assertEqual(self.call('/api/judge/enter','POST',{'token':old},judge_as='')['status'],401)
        entered=self.call('/api/judge/enter','POST',{'token':fresh},judge_as='')
        self.assertEqual(entered['status'],200)
        self.judge_cookies['demo-judge-1']=entered['headers']['Set-Cookie'].split(';')[0]
        self.assertEqual(self.call('/api/scores')['json']['scores'][0]['value'],23)

    def test_rename_keeps_entry_disable_revokes_even_after_reenable(self):
        original=self.entry_tokens['demo-judge-1']
        self.call('/api/admin/judges','POST',{'id':'demo-judge-1','name':'正式评委','active':True},admin=True)
        self.assertEqual(self.call('/api/bootstrap')['json']['current_judge']['name'],'正式评委')
        entry=self.call('/api/admin/judge-entry','POST',{'judge_id':'demo-judge-1'},admin=True)['json']
        self.assertEqual(entry['token'],original)
        self.assertEqual(entry['name'],'正式评委')
        self.call('/api/admin/judges','POST',{'id':'demo-judge-1','name':'正式评委','active':False},admin=True)
        self.assertEqual(self.call('/api/scores')['status'],401)
        self.assertEqual(self.call('/api/judge/enter','POST',{'token':original},judge_as='')['status'],401)
        self.assertEqual(self.call('/api/admin/judge-entry','POST',{'judge_id':'demo-judge-1'},admin=True)['status'],400)
        self.call('/api/admin/judges','POST',{'id':'demo-judge-1','name':'正式评委','active':True},admin=True)
        self.assertEqual(self.call('/api/judge/enter','POST',{'token':original},judge_as='')['status'],401)
        fresh=self.call('/api/admin/judge-entry','POST',{'judge_id':'demo-judge-1'},admin=True)['json']['token']
        self.assertNotEqual(original,fresh)

    def test_invalid_entry_clears_previous_identity_and_session_expires(self):
        original=self.entry_tokens['demo-judge-1']
        for token in ['made-up','demo-judge-2',original[:-1]+('a' if original[-1]!='a' else 'b')]:
            r=self.call('/api/judge/enter','POST',{'token':token})
            self.assertEqual(r['status'],401)
            self.assertIn('Max-Age=0',r['headers']['Set-Cookie'])
        self.assertEqual(self.call('/api/judge/enter','POST',{'token':''})['status'],400)
        with app.connect() as db:
            db.execute("UPDATE judge_sessions SET expires=0 WHERE judge_id='demo-judge-1'")
        self.assertEqual(self.call('/api/scores')['status'],401)
        self.assertIsNone(self.call('/api/bootstrap')['json']['current_judge'])

    def test_entry_session_cookie_flags(self):
        old_secure=app.COOKIE_SECURE
        try:
            for secure in [False,True]:
                app.COOKIE_SECURE=secure
                r=self.call('/api/judge/enter','POST',{'token':self.entry_tokens['demo-judge-1']},judge_as='')
                cookie=r['headers']['Set-Cookie']
                for flag in ['HttpOnly','SameSite=Strict','Path=/api','Max-Age=604800']:
                    self.assertIn(flag,cookie)
                self.assertEqual('; Secure' in cookie,secure)
        finally: app.COOKIE_SECURE=old_secure

    def test_public_entry_url_validation_and_persistence(self):
        for url,expected in [('https://score.example.com','https://score.example.com/'),(' http://192.168.1.2:8001/ ','http://192.168.1.2:8001/'),('http://[::1]:8089','http://[::1]:8089/')]:
            r=self.call('/api/admin/entry-url','POST',{'url':url},admin=True)
            self.assertEqual(r['status'],200)
            self.assertEqual(r['json']['url'],expected)
        for url in ['javascript:alert(1)','https://user:pass@example.com','https://example.com/admin','https://example.com/?a=1','https://example.com/#x','http://example.com:99999','http://[bad','http://two words','http://good.com\\@bad.com','/','',None]:
            self.assertEqual(self.call('/api/admin/entry-url','POST',{'url':url},admin=True)['status'],400,msg=str(url))
        self.assertEqual(self.call('/api/admin/state',admin=True)['json']['settings']['public_base_url'],'http://[::1]:8089/')

    def test_rubric_weights(self):
        self.assertEqual([d['max'] for d in app.CATEGORIES['agent']['dimensions']],[25,25,25,20,5])
        self.assertEqual(len(app.CATEGORIES['solution']['dimensions']),4)

    def presentation(self,action='select',**values):
        return self.call('/api/admin/presentation','POST',{'round':'preliminary','action':action,**values},admin=True)

    def test_presentation_selection_order_clear_and_persistence(self):
        self.assertIsNone(self.call('/api/presentation')['json']['project'])
        self.assertEqual(self.presentation(project_id='demo-project-3')['json']['project']['id'],'demo-project-3')
        self.assertEqual(self.presentation('next',category='solution',expected_project_id='demo-project-3')['json']['project']['id'],'demo-project-4')
        self.assertEqual(self.presentation('next',category='solution')['status'],400)
        self.assertEqual(self.presentation('previous',category='solution')['json']['project']['id'],'demo-project-3')
        self.assertEqual(self.presentation('previous',category='solution')['status'],400)
        self.assertEqual(self.presentation(project_id='demo-project-5',expected_project_id='demo-project-4')['status'],409)
        app.init_db()
        self.assertEqual(self.call('/api/bootstrap')['json']['presentation']['project']['id'],'demo-project-3')
        self.assertEqual(self.presentation('clear')['json']['project'],None)
        self.assertEqual(self.presentation('next',category='agent')['json']['project']['id'],'demo-project-5')

    def test_presentation_auth_phase_and_removed_projects(self):
        self.assertEqual(self.call('/api/presentation',judge_as='')['status'],401)
        self.assertEqual(self.call('/api/admin/presentation','POST',{'round':'preliminary','project_id':'demo-project-1'})['status'],401)
        self.presentation(project_id='demo-project-1')
        self.assertIsNone(self.call('/api/bootstrap',judge_as='')['json']['presentation']['project'])
        self.configure_final('demo-project-3',80);self.switch_round()
        self.assertIsNone(self.call('/api/presentation')['json']['project'])
        self.assertEqual(self.presentation(project_id='demo-project-3')['status'],409)
        self.assertEqual(self.presentation(project_id='demo-project-1',round='final')['status'],400)
        result=self.presentation(project_id='demo-project-3',round='final')
        self.assertEqual(result['status'],200)
        self.assertEqual(set(result['json']['project']),{'id','category','number','title','person','department'})
        self.configure_final('demo-project-3',80,False)
        self.assertIsNone(self.call('/api/presentation')['json']['project'])
        self.configure_final('demo-project-3',80,True)
        self.assertIsNone(self.call('/api/presentation')['json']['project'])
        self.switch_round('preliminary')
        self.assertEqual(self.call('/api/presentation')['json']['project']['id'],'demo-project-1')
        p=next(p for p in self.call('/api/admin/state',admin=True)['json']['projects'] if p['id']=='demo-project-1')
        self.call('/api/admin/projects','POST',{**p,'active':False},admin=True)
        self.assertIsNone(self.call('/api/presentation')['json']['project'])

    def project_import(self,category='agent',mode='preview',**values):
        return self.call('/api/admin/projects-import','POST',{'category':category,'mode':mode,**values},admin=True)

    def test_project_csv_preview_import_module_isolation_and_atomic_validation(self):
        content='序号,项目主题,姓名,部门\n12,新智能体,张三,研发部\n13,智能助手,李四,技术部\n'.encode('utf-8-sig')
        preview=self.project_import(filename='项目.csv',data=base64.b64encode(content).decode())
        self.assertEqual(preview['status'],200)
        with app.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM projects').fetchone()[0],6)
        result=self.project_import(mode='import',rows=preview['json']['rows'])
        self.assertEqual(result['json']['imported'],2)
        self.assertEqual(self.project_import(mode='import',rows=preview['json']['rows'])['status'],400)
        other=self.project_import(category='solution',mode='import',rows=preview['json']['rows'])
        self.assertEqual(other['status'],200)
        mixed=[{'number':15,'title':'有效项目','person':'测试','department':'部门'},{'number':16,'title':'缺姓名','person':'','department':'部门'}]
        self.assertEqual(self.project_import(mode='import',rows=mixed)['status'],400)
        with app.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM projects WHERE number=15").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM projects WHERE category='prompt'").fetchone()[0],2)

    def filled_project_template(self,category='agent',formula=False):
        target=app.ROOT/'static'/'templates'/f'projects-{category}.xlsx'
        out=io.BytesIO();ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
        with zipfile.ZipFile(target) as source,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as dest:
            for item in source.infolist():
                raw=source.read(item.filename)
                if item.filename=='xl/worksheets/sheet1.xml':
                    sheet=ET.fromstring(raw);row=sheet.find(f'{{{ns}}}sheetData/{{{ns}}}row[@r="5"]')
                    if row is None:row=ET.SubElement(sheet.find(f'{{{ns}}}sheetData'),f'{{{ns}}}row',r='5')
                    for col,value in zip('ABCD',['20','Excel导入项目','王五','综合部']):
                        cell=row.find(f'{{{ns}}}c[@r="{col}5"]')
                        if cell is None:cell=ET.SubElement(row,f'{{{ns}}}c',r=col+'5')
                        for child in list(cell):cell.remove(child)
                        cell.set('t','inlineStr');ET.SubElement(ET.SubElement(cell,f'{{{ns}}}is'),f'{{{ns}}}t').text=value
                        if formula and col=='A':ET.SubElement(cell,f'{{{ns}}}f').text='ROW()-4'
                    raw=ET.tostring(sheet,encoding='utf-8',xml_declaration=True)
                dest.writestr(item.filename,raw)
        return out.getvalue()

    def test_project_excel_templates_and_preview(self):
        for category in ['agent','solution','prompt']:
            response=self.call('/api/admin/project-template?category='+category,admin=True)
            self.assertEqual(response['status'],200)
            self.assertTrue(response['raw'].startswith(b'PK'))
            preview=self.project_import(category,filename='名单.xlsx',data=base64.b64encode(self.filled_project_template(category)).decode())
            self.assertEqual(preview['status'],200,msg=str(preview.get('json')))
            self.assertEqual(preview['json']['rows'][0]['title'],'Excel导入项目')
        wrong=self.project_import('solution',filename='错误模块.xlsx',data=base64.b64encode(self.filled_project_template()).decode())
        self.assertEqual(wrong['status'],400)
        formula=self.project_import(filename='公式.xlsx',data=base64.b64encode(self.filled_project_template(formula=True)).decode())
        self.assertEqual(formula['status'],400)
        self.assertIn('公式',formula['json']['error'])

    def test_project_import_restrictions_and_auth(self):
        self.assertEqual(self.call('/api/admin/project-template?category=agent')['status'],401)
        self.assertEqual(self.call('/api/admin/projects-import','POST',{'category':'agent','mode':'import','rows':[]})['status'],401)
        self.assertEqual(self.project_import(filename='坏文件.xlsx',data=base64.b64encode(b'bad zip').decode())['status'],400)
        csv='序号,项目主题,姓名,部门,所属模块\n9,主题,姓名,部门,解决方案设计'.encode()
        self.assertEqual(self.project_import(filename='跨模块.csv',data=base64.b64encode(csv).decode())['status'],400)
        rows=[{'number':99,'title':'项目','person':'姓名','department':'部门'}]*2
        self.assertEqual(self.project_import(mode='import',rows=rows)['status'],400)
        self.configure_final();self.switch_round()
        self.assertEqual(self.call('/api/admin/project-template?category=prompt',admin=True)['status'],400)
        self.assertEqual(self.project_import('prompt',mode='import',rows=[rows[0]])['status'],400)

    def configure_final(self,project='demo-project-3',initial=80,active=True):
        return self.call('/api/admin/finalists','POST',{'project_id':project,'preliminary_score':initial,'active':active},admin=True)

    def switch_round(self,name='final'):
        return self.call('/api/admin/round','POST',{'round':name},admin=True)

    def final_score(self,k,value,judge='demo-judge-1',project='demo-project-3',**extra):
        return self.call('/api/score','PUT',{'round':'final','judge_id':judge,'project_id':project,'dimension':k,'value':value,**extra},judge_as=judge)

    def final_row(self,project='demo-project-3'):
        return next(p for c in self.call('/api/leaderboard',judge_as='')['json']['categories'] for p in c['projects'] if p['id']==project)

    def test_final_default_categories_and_optional_manual_initial_score(self):
        self.assertEqual(self.configure_final('missing-project')['status'],404)
        self.assertEqual(self.switch_round()['status'],400)
        self.assertEqual(self.call('/api/admin/finalists','POST',{'project_id':'demo-project-3','initial_source':'manual','preliminary_score':None},admin=True)['status'],400)
        for value in [-1,101,True,'80',79.123,float('nan')]:
            self.assertEqual(self.configure_final(initial=value)['status'],400,msg=str(value))
        self.assertEqual(self.configure_final(initial=0)['status'],200)
        self.assertEqual(self.switch_round()['status'],200)
        public=self.call('/api/bootstrap')['json']
        self.assertEqual(public['round'],'final')
        self.assertEqual({c['id'] for c in public['categories']},{'agent','solution'})
        self.assertEqual([p['id'] for p in public['projects']],['demo-project-3'])
        self.assertNotIn('finalists',public)
        self.assertEqual(self.final_score(0,10,project='demo-project-1')['status'],403)
        self.assertEqual(self.final_score(0,10,project='demo-project-4')['status'],403)
        self.assertEqual(self.call('/api/admin/finalists','POST',{'project_id':'demo-project-3','preliminary_score':90},judge_as='')['status'],401)
        self.assertEqual(self.call('/api/admin/round','POST',{'round':'final'},judge_as='')['status'],401)

    def test_rounds_and_write_versions_are_separate_and_old_writes_blocked(self):
        self.score(0,12,project='demo-project-3')
        self.configure_final();self.switch_round()
        self.assertEqual(self.score(0,20,project='demo-project-3')['status'],409)
        self.assertEqual(self.final_score(0,27,client='same-client',version=1)['status'],200)
        self.assertEqual(self.final_score(0,15,client='same-client',version=0)['json']['stale'],True)
        self.assertEqual(self.call('/api/scores?round=final')['json']['scores'][0]['value'],27)
        self.assertEqual(self.call('/api/scores?round=preliminary')['json']['scores'][0]['value'],12)
        self.switch_round('preliminary')
        self.assertEqual(self.final_score(0,10)['status'],409)
        app.init_db()
        self.assertEqual(self.call('/api/scores?round=final')['json']['scores'][0]['value'],27)

    def test_leaderboard_weights_average_and_wait_for_all(self):
        self.only_judges("demo-judge-1","demo-judge-2")
        # Existing preliminary scores are deliberately unrelated to the manual 80.
        for k,d in enumerate(app.CATEGORIES['solution']['dimensions']): self.score(k,d['max'],project='demo-project-3')
        self.configure_final(initial=80);self.switch_round()
        self.final_score(0,30)
        self.assertIsNone(self.final_row()['score'])
        for k,value in enumerate([30,25,25,20]): self.final_score(k,value)
        self.assertIsNone(self.final_row()['score'])
        self.final_score(0,0,'demo-judge-2')
        self.assertIsNone(self.final_row()['score'])
        for k,value in enumerate([20,20,20,20]): self.final_score(k,value,'demo-judge-2')
        self.assertEqual(self.final_row()['score'],83)  # 80*.7 + mean(100,80)*.3
        self.final_score(0,None,'demo-judge-2')
        self.assertIsNone(self.final_row()['score'])
        self.configure_final(initial=90)
        self.assertIsNone(self.final_row()['score'])
        self.final_score(0,20,'demo-judge-2')
        self.assertEqual(self.final_row()['score'],90)
        self.assertEqual(self.final_row()['rank'],1)

    def test_final_subitems_partial_zero_and_clear(self):
        self.only_judges("demo-judge-1")
        self.configure_final(initial=0);self.switch_round()
        dims=app.CATEGORIES['solution']['dimensions']
        for k,d in enumerate(dims):
            for si,_ in enumerate(d['subitems']): self.assertEqual(self.final_score(k,0,subdimension=si)['status'],200)
        self.assertEqual(self.final_row()['score'],0)
        self.assertEqual(self.final_row()['rank'],1)
        self.final_score(0,None,subdimension=0)
        self.assertIsNone(self.final_row()['score'])
        self.assertIsNone(self.final_row()['rank'])
        self.final_score(0,1,subdimension=0)
        self.assertEqual(self.final_row()['score'],0.3)
        with app.connect() as db:db.execute("UPDATE judges SET active=1 WHERE id='demo-judge-2'")
        self.assertEqual(self.final_score(0,2,subdimension=0,judge='demo-judge-2')['status'],200)
        self.assertIsNone(self.final_row()['score'])  # Newly required panel member has not completed.

    def test_leaderboard_ties_categories_archive_and_public_fields(self):
        self.only_judges("demo-judge-1")
        for project in ['demo-project-3','demo-project-4','demo-project-5','demo-project-6']:
            self.configure_final(project,80)
        self.switch_round()
        for project in ['demo-project-3','demo-project-4','demo-project-5']:
            for k,d in enumerate(app.CATEGORIES['agent' if project.endswith('5') else 'solution']['dimensions']): self.final_score(k,d['max'],project=project)
        rows=self.call('/api/leaderboard',judge_as='')['json']['categories']
        self.assertEqual([g['id'] for g in rows],['agent','solution'])
        self.assertEqual([r['rank'] for r in rows[1]['projects']],[1,1])
        self.assertEqual(rows[0]['projects'][0]['rank'],1)
        self.assertIsNone(rows[0]['projects'][1]['rank'])
        self.assertEqual(set(rows[1]['projects'][0]),{'id','title','person','department','score','rank'})
        self.configure_final('demo-project-4',80,False)
        self.assertNotIn('demo-project-4',[p['id'] for p in self.call('/api/leaderboard')['json']['categories'][1]['projects']])
        self.assertEqual(self.final_score(0,10,project='demo-project-4')['status'],403)
        self.configure_final('demo-project-4',80,True)
        self.assertEqual(self.final_row('demo-project-4')['score'],86)
        with app.connect() as db:db.execute("UPDATE projects SET active=0 WHERE id='demo-project-4'")
        self.assertNotIn('demo-project-4',[p['id'] for p in self.call('/api/leaderboard')['json']['categories'][1]['projects']])

    def test_weighted_score_rounding_and_final_excel(self):
        self.only_judges("demo-judge-1")
        self.configure_final(initial=80.25);self.switch_round()
        for k,value in enumerate([30,20,20,20]): self.final_score(k,value)
        self.assertEqual(self.final_row()['score'],83.18)  # Decimal half-up at the final step.
        self.assertEqual(self.call('/leaderboard',judge_as='')['status'],200)
        report=self.call('/api/admin/export',admin=True)
        with zipfile.ZipFile(io.BytesIO(report['raw'])) as archive:
            workbook=archive.read('xl/workbook.xml').decode()
            self.assertNotIn('提示词设计',workbook)
            self.assertIn('智能体设计',workbook);self.assertIn('解决方案设计',workbook)
            all_sheets=''.join(archive.read(n).decode() for n in archive.namelist() if n.startswith('xl/worksheets/sheet') and n.endswith('.xml'))
            for value in ['最终分数','初试平均分','复试平均分','83.18']:self.assertIn(value,all_sheets)

    def test_final_export_mean_uses_same_half_up_rounding(self):
        from excel_export import build_report
        with app.connect() as db:data=app.snapshot(db)
        data['round']='final';data['projects']=[p for p in data['projects'] if p['id']=='demo-project-3']
        data['judges']=[{'id':str(i),'name':f'评委{i}','active':1} for i in range(8)]
        data['finalists']=[{'project_id':'demo-project-3','preliminary_score':80,'active':1}]
        data['combined_scores']={'demo-project-3':{'score':56.04}}
        records=[{'project_id':'demo-project-3','judge_id':str(i),'values':{str(k):int(i==0 and k==0) for k in range(4)},'subvalues':{},'complete':True,'total':int(i==0),'subtotal':int(i==0)} for i in range(8)]
        report=build_report(data,records,[app.CATEGORIES['solution']])
        with zipfile.ZipFile(io.BytesIO(report)) as archive:
            xml=archive.read('xl/worksheets/sheet1.xml').decode()
            self.assertIn('<v>0.13</v>',xml)  # 1/8 rounds up, consistently with leaderboard means.
            self.assertNotIn('<v>0.12</v>',xml)

    def test_save_restore_and_update(self):
        self.assertEqual(self.score(0,20)['status'],200)
        self.score(0,23)
        r=self.call('/api/scores?judge=demo-judge-1')['json']['scores']
        self.assertEqual(len(r),1)
        self.assertEqual(r[0]['value'],23)
        # Reinitialization must preserve saved data.
        app.init_db()
        self.assertEqual(self.call('/api/scores?judge=demo-judge-1')['json']['scores'][0]['value'],23)

    def test_zero_is_valid_and_clear_removes(self):
        self.score(0,0)
        self.assertEqual(self.call('/api/scores?judge=demo-judge-1')['json']['scores'][0]['value'],0)
        self.score(0,None)
        self.assertEqual(self.call('/api/scores?judge=demo-judge-1')['json']['scores'],[])

    def test_judges_and_projects_independent(self):
        self.score(0,10)
        self.score(0,20,'demo-judge-2')
        self.score(0,15,project='demo-project-2')
        r=self.call('/api/scores?judge=demo-judge-1')['json']['scores']
        self.assertEqual({s['project_id']:s['value'] for s in r},{'demo-project-1':10,'demo-project-2':15})

    def test_invalid_scores(self):
        for v in (-1,0.5,18.25,25.01,True,'20',1.234,float('nan'),float('inf')):
            self.assertEqual(self.score(0,v)['status'],400,msg=str(v))
        for k in (-1,5,True,'0'):
            self.assertEqual(self.score(k,10)['status'],400)

    def test_bands_cover_all_integer_scores(self):
        data=self.call('/api/bootstrap')['json']
        for c in data['categories']:
            for d in c['dimensions']:
                points=[v for b in d['bands'] for v in range(b['min'],b['max']+1)]
                self.assertEqual(points,list(range(d['max']+1)))

    def test_admin_bands_persist_and_export_without_changing_scores(self):
        self.score(0,20)
        self.login()
        bands=[{'min':0,'max':10,'label':'需完善','description':'结果需要较多修改。'},
               {'min':11,'max':20,'label':'良好','description':'结果基本可用。'},
               {'min':21,'max':25,'label':'优秀','description':'结果可直接使用。'}]
        payload={'category':'prompt','dimension':0,'bands':bands}
        self.assertEqual(self.call('/api/admin/score-bands','POST',payload)['status'],401)
        self.assertEqual(self.call('/api/admin/score-bands','POST',payload,admin=True)['status'],200)
        app.init_db()
        d=self.call('/api/bootstrap')['json']['categories'][0]['dimensions'][0]
        self.assertEqual(d['bands'],bands)
        self.assertEqual(d['max'],25)
        self.assertEqual(self.call('/api/scores?judge=demo-judge-1')['json']['scores'][0]['value'],20)
        with zipfile.ZipFile(io.BytesIO(self.call('/api/admin/export',admin=True)['raw'])) as z:
            self.assertIn('结果可直接使用。',z.read('xl/worksheets/sheet1.xml').decode())

    def test_invalid_bands_leave_existing_rules_intact(self):
        self.login()
        original=self.call('/api/bootstrap')['json']['categories'][0]['dimensions'][0]['bands']
        for bounds in [[(0,10),(10,20),(21,25)],[(0,9),(11,20),(21,25)],[(1,10),(11,25)],[(0,26)],[(0,24)],[(0,10.5),(11,25)]]:
            bands=[{'min':lo,'max':hi,'label':'测试','description':'测试说明'} for lo,hi in bounds]
            result=self.call('/api/admin/score-bands','POST',{'category':'prompt','dimension':0,'bands':bands},admin=True)
            self.assertEqual(result['status'],400)
        self.assertEqual(self.call('/api/bootstrap')['json']['categories'][0]['dimensions'][0]['bands'],original)

    def test_legacy_fractional_score_is_not_rounded_on_upgrade(self):
        with app.connect() as db:
            db.execute('INSERT INTO scores VALUES (?,?,?,?,?)',('demo-judge-1','demo-project-1',0,18.25,app.now()))
        app.init_db()
        self.assertEqual(self.call('/api/scores?judge=demo-judge-1')['json']['scores'][0]['value'],18.25)
        self.assertEqual(self.score(0,18.25)['status'],400)
        self.assertEqual(self.score(0,18)['status'],200)

    def subscore(self,k,sub,value,judge='demo-judge-1',project='demo-project-1',**extra):
        return self.call('/api/score','PUT',{'judge_id':judge,'project_id':project,'dimension':k,'subdimension':sub,'value':value,**extra},judge_as=judge)

    def record(self):
        with app.connect() as db:
            return next(r for r in app.all_records(db)[1] if r['project_id']=='demo-project-1' and r['judge_id']=='demo-judge-1')

    def test_subitem_weights_and_criteria(self):
        data=self.call('/api/bootstrap')['json']
        count=0
        for c in data['categories']:
            self.assertEqual(sum(d['max'] for d in c['dimensions']),100)
            for d in c['dimensions']:
                self.assertEqual(sum(sub['max'] for sub in d['subitems']),d['max'])
                for sub in d['subitems']:
                    count+=1
                    self.assertEqual(len(sub['elements']),5)
                    self.assertIn(sub['max'],[5,10])
                    self.assertTrue(sub['definition'] and sub['scoring_rule'])
        self.assertEqual(count,35)
        self.assertEqual([sub['max'] for sub in data['categories'][0]['dimensions'][0]['subitems']],[5,10,10])

    def test_subscores_aggregate_and_partial_excluded(self):
        for k,d in enumerate(app.CATEGORIES['prompt']['dimensions']):
            self.assertEqual(self.subscore(k,0,d['subitems'][0]['max'])['status'],200)
        r=self.record();self.assertFalse(r['complete']);self.assertIsNone(r['total'])
        for k,d in enumerate(app.CATEGORIES['prompt']['dimensions']):
            for si,sub in enumerate(d['subitems']):self.subscore(k,si,sub['max'])
        r=self.record();self.assertTrue(r['complete']);self.assertEqual(r['total'],100)
        self.assertEqual(r['values']['0'],25);self.assertEqual(r['subvalues']['0'],{'0':5,'1':10,'2':10})
        self.subscore(0,1,0)
        self.assertEqual(self.record()['total'],90)
        self.subscore(0,2,None)
        r=self.record();self.assertFalse(r['complete']);self.assertEqual(r['values']['0'],5)
        self.assertEqual(len(self.call('/api/scores?judge=demo-judge-1')['json']['subscores']),11)

    def test_subscore_invalid_values_and_stale_writes(self):
        for sub,value in [(0,6),(1,11),(0,1.5),(3,1),(-1,1),(True,1),(None,1)]:
            self.assertEqual(self.subscore(0,sub,value)['status'],400)
        self.subscore(0,0,5,client='sub-client',version=200)
        self.subscore(0,1,8,client='sub-client',version=100)
        self.assertEqual(self.record()['values']['0'],13)
        result=self.subscore(0,0,2,client='sub-client',version=100)
        self.assertTrue(result['json']['stale']);self.assertEqual(self.record()['values']['0'],13)
        self.subscore(0,0,None,client='sub-client',version=300)
        self.subscore(0,0,5,client='sub-client',version=200)
        self.assertEqual(self.record()['values']['0'],8)
        self.assertEqual(self.score(0,25)['status'],400)

    def test_legacy_parent_not_invented_as_subscores(self):
        self.score(0,23);app.init_db()
        r=self.record();self.assertEqual(r['values']['0'],23);self.assertEqual(r['subvalues'],{})
        self.subscore(0,0,4)
        r=self.record();self.assertEqual(r['values']['0'],4);self.assertEqual(r['subvalues']['0'],{'0':4})
        self.subscore(0,0,None)
        self.assertNotIn('0',self.record()['values'])

    def test_subitem_notes_persist_and_export(self):
        sub=app.CATEGORIES['prompt']['dimensions'][0]['subitems'][0]
        body={'category':'prompt','dimension':0,'subdimension':0,'name':sub['name'],'definition':'检查必备内容是否齐全。','elements':['材料覆盖要素'+str(i) for i in range(1,6)]}
        self.assertEqual(self.call('/api/admin/subitem-rules','POST',body)['status'],401)
        self.login()
        self.assertEqual(self.call('/api/admin/subitem-rules','POST',body,admin=True)['status'],200)
        self.assertEqual(self.call('/api/admin/subitem-rules','POST',{**body,'elements':['缺少要素']},admin=True)['status'],400)
        app.init_db()
        saved=self.call('/api/bootstrap')['json']['categories'][0]['dimensions'][0]['subitems'][0]
        self.assertEqual(saved['definition'],body['definition']);self.assertEqual(saved['max'],5)
        for si,value in enumerate([4,8,9]):self.subscore(0,si,value)
        r=self.call('/api/admin/export',admin=True)
        with zipfile.ZipFile(io.BytesIO(r['raw'])) as z:
            xml=z.read('xl/worksheets/sheet1.xml').decode()
            self.assertIn('子项评分明细',xml);self.assertIn('材料覆盖要素5',xml)
            self.assertIn('内容完整性',xml)

    def test_wrong_ids(self):
        self.assertEqual(self.score(0,10,judge='missing')['status'],401)
        self.assertEqual(self.score(0,10,project='missing')['status'],400)

    def test_stale_unload_requests_cannot_overwrite_newer_score(self):
        b={'judge_id':'demo-judge-1','project_id':'demo-project-1','dimension':0,'client':'browser-a'}
        self.call('/api/score','PUT',{**b,'value':20,'version':200})
        r=self.call('/api/score','PUT',{**b,'value':10,'version':100})
        self.assertTrue(r['json']['stale'])
        self.assertEqual(self.call('/api/scores?judge=demo-judge-1')['json']['scores'][0]['value'],20)
        self.call('/api/score','PUT',{**b,'value':None,'version':300})
        self.call('/api/score','PUT',{**b,'value':20,'version':200})
        self.assertEqual(self.call('/api/scores?judge=demo-judge-1')['json']['scores'],[])

    def test_completion_and_partial_not_averaged(self):
        self.score(0,10,'demo-judge-2')
        for k,v in enumerate([25,25,20,20,10]):self.score(k,v)
        with app.connect() as db:
            _,records=app.all_records(db)
        r1=next(r for r in records if r['project_id']=='demo-project-1' and r['judge_id']=='demo-judge-1')
        r2=next(r for r in records if r['project_id']=='demo-project-1' and r['judge_id']=='demo-judge-2')
        self.assertTrue(r1['complete']);self.assertEqual(r1['total'],100)
        self.assertFalse(r2['complete']);self.assertIsNone(r2['total'])

    def test_admin_requires_password(self):
        for path in ('/api/admin/state','/api/admin/export'):
            self.assertEqual(self.call(path)['status'],401)
        self.assertEqual(self.call('/api/admin/login','POST',{'password':'wrong'})['status'],401)
        self.login()
        self.assertEqual(self.call('/api/admin/state',admin=True)['status'],200)
        self.call('/api/admin/logout','POST',{},admin=True)
        self.assertEqual(self.call('/api/admin/state',admin=True)['status'],401)

    def test_cross_origin_write_rejected(self):
        r=self.call('/api/score','PUT',{},origin='https://evil.example')
        self.assertEqual(r['status'],403)

    def test_archive_preserves_scores_and_blocks_new_writes(self):
        self.score(0,10)
        self.login()
        p=self.call('/api/admin/state',admin=True)['json']['projects'][0]
        p=next(p for p in self.call('/api/admin/state',admin=True)['json']['projects'] if p['id']=='demo-project-1')
        self.call('/api/admin/projects','POST',{**p,'active':False},admin=True)
        self.assertEqual(self.score(0,20)['status'],400)
        self.assertNotIn(p['id'],[p['id'] for p in self.call('/api/bootstrap')['json']['projects']])
        with app.connect() as db:self.assertEqual(db.execute('SELECT value FROM scores').fetchone()[0],10)

    def test_scored_project_category_locked(self):
        self.score(0,10);self.login()
        p=next(p for p in self.call('/api/admin/state',admin=True)['json']['projects'] if p['id']=='demo-project-1')
        self.assertEqual(self.call('/api/admin/projects','POST',{**p,'category':'agent'},admin=True)['status'],400)

    def test_export_averages_and_blank_partial(self):
        for k,v in enumerate([25,25,20,20,10]):self.score(k,v)
        for k,v in enumerate([20,20,15,15,10]):self.score(k,v,'demo-judge-2')
        self.score(0,5,'demo-judge-3')
        self.login();r=self.call('/api/admin/export',admin=True)
        self.assertEqual(r['status'],200)
        n={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with zipfile.ZipFile(io.BytesIO(r['raw'])) as z:
            for filename in z.namelist():ET.fromstring(z.read(filename))
            workbook=ET.fromstring(z.read('xl/workbook.xml'))
            names=[x.attrib['name'] for x in workbook.findall('m:sheets/m:sheet',n)]
            self.assertEqual(names,['提示词设计','解决方案设计','智能体设计'])
            def cells(sheet):
                tree=ET.fromstring(z.read('xl/worksheets/sheet'+str(sheet)+'.xml'))
                values={}
                for c in tree.findall('.//m:c',n):
                    v=c.find('m:v',n);t=c.find('m:is/m:t',n)
                    values[c.attrib['r']]=float(v.text) if v is not None else t.text if t is not None else None
                return values,tree
            values,tree=cells(1)
            self.assertEqual(values['I4'],90)
            self.assertEqual(values['J4'],2)
            self.assertEqual(values['F4'],100)
            self.assertEqual(values['G4'],80)
            self.assertIsNone(values['H4'])
            self.assertEqual(values['A4'],1)
            self.assertEqual(values['A5'],'未评分')
            headerrow=next(int(ref[1:]) for ref,v in values.items() if ref.startswith('B') and str(v).startswith('输出效果'))
            self.assertIn('提示词设计质量',values['C'+str(headerrow)])
            self.assertEqual(values['B'+str(headerrow+1)],25)
            self.assertEqual(values['B'+str(headerrow+2)],20)
            self.assertEqual(values['B'+str(headerrow+3)],5)
            self.assertIsNone(values['C'+str(headerrow+3)])
            self.assertIsNone(values['H'+str(headerrow+3)])
            self.assertEqual(values['H'+str(headerrow+4)],90)
            self.assertTrue(any('本模块评分细则' in str(v) for v in values.values()))
            self.assertTrue(any('结果正确切题' in str(v) for v in values.values()))
            self.assertTrue(tree.findall('m:hyperlinks/m:hyperlink',n))
            project_link=next(h for h in tree.findall('m:hyperlinks/m:hyperlink',n) if h.attrib['ref']=='C4')
            target_ref=project_link.attrib['location'].rsplit('!',1)[1]
            self.assertIn('项目 02',values[target_ref])
            last_link=next(h for h in tree.findall('m:hyperlinks/m:hyperlink',n) if h.attrib['ref']=='C5')
            self.assertIn('本模块评分细则',values[last_link.attrib['location'].rsplit('!',1)[1]])
            for i in range(1,4):
                values,sheet_tree=cells(i)
                self.assertIsNone(sheet_tree.find('m:sheetViews/m:sheetView/m:pane',n))
                self.assertFalse(any('维度1' in str(v) or '维度2' in str(v) for v in values.values()))

    def test_export_ranking_order_and_ties(self):
        from excel_export import build_report
        def rank_rows():
            with app.connect() as db:
                data,records=app.all_records(db)
            raw=build_report(data,records,app.RUBRICS)
            n={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                tree=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
                return {c.attrib['r']:c.find('m:v',n).text for c in tree.findall('.//m:c',n) if c.find('m:v',n) is not None}
        for k,v in enumerate([20,20,15,15,10]):self.score(k,v)
        for k,v in enumerate([25,25,20,20,10]):self.score(k,v,project='demo-project-2')
        values=rank_rows()
        self.assertEqual(values['B4'],'2')
        self.assertEqual(values['I4'],'100.0')
        self.assertEqual(values['B5'],'1')
        self.assertEqual(values['A5'],'2')
        for k,v in enumerate([25,25,20,20,10]):self.score(k,v)
        values=rank_rows()
        self.assertEqual(values['A4'],'1')
        self.assertEqual(values['A5'],'1')

    def test_general_mode_selection_and_switch_preserve_bindings(self):
        with patch.dict(os.environ,{'WECOM_CORP_ID':'test-corp'}):
            binding={'id':'demo-judge-1','name':'演示评委1','wecom_userid':'person1'}
            self.assertEqual(self.call('/api/admin/judges','POST',binding,admin=True)['status'],200)
            self.assertEqual(self.call('/api/judge/select','POST',{'judge_id':'demo-judge-1'},judge_as='')['status'],403)
            self.assertEqual(self.call('/api/admin/entry-mode','POST',{'mode':'general'},admin=True)['status'],200)
            data=self.call('/api/bootstrap',judge_as='')['json']
            self.assertEqual(len(data['judges']),3)
            selected=self.call('/api/judge/select','POST',{'judge_id':'demo-judge-2'},judge_as='')
            cookie=selected['headers']['Set-Cookie'].split(';')[0]
            self.assertEqual(self.call('/api/bootstrap',cookie=cookie)['json']['current_judge']['id'],'demo-judge-2')
            self.assertEqual(self.call('/api/score','PUT',{'project_id':'demo-project-1','dimension':0,'value':20},cookie=cookie)['status'],200)
            self.call('/api/admin/entry-mode','POST',{'mode':'bound'},admin=True)
            self.assertIsNone(self.call('/api/bootstrap',cookie=cookie)['json']['current_judge'])
            self.assertEqual(self.call('/api/scores',cookie=cookie)['status'],401)
            self.assertEqual(self.call('/api/scores',judge_as='demo-judge-2')['json']['scores'][0]['value'],20)
            self.call('/api/admin/entry-mode','POST',{'mode':'general'},admin=True)
            self.assertIsNone(self.call('/api/bootstrap',cookie=cookie)['json']['current_judge'])
            self.assertEqual(self.call('/api/admin/state',admin=True)['json']['wecom']['bindings'],[{'userid':'person1','judge_id':'demo-judge-1'}])

    def test_wecom_state_binding_callback_and_existing_scores(self):
        config={'WECOM_CORP_ID':'test-corp','WECOM_AGENT_ID':'1000001','WECOM_SECRET':'fake-test-secret','WECOM_PUBLIC_URL':'http://score.example.com:8089/'}
        with patch.dict(os.environ,config):
            self.score(0,23)
            self.call('/api/admin/judges','POST',{'id':'demo-judge-1','name':'演示评委1','wecom_userid':'person1'},admin=True)
            start=self.call('/api/wecom/start',judge_as='')
            self.assertEqual(start['status'],302)
            params=parse_qs(urlparse(start['headers']['Location']).query)
            self.assertEqual(params['scope'],['snsapi_base'])
            self.assertEqual(params['redirect_uri'],['http://score.example.com:8089/api/wecom/callback'])
            self.assertNotIn('fake-test-secret',start['headers']['Location'])
            browser=start['headers']['Set-Cookie'].split(';')[0]
            callback='/api/wecom/callback?state='+params['state'][0]+'&code=test-code'
            with patch('wecom_auth.identify',return_value=('test-corp','person1')) as identify:
                wrong=self.call(callback,cookie='')
                self.assertEqual(wrong['headers']['Location'],'/?wecom=state');identify.assert_not_called()
                response=self.call(callback,cookie=browser)
                self.assertEqual(response['headers']['Location'],'/?wecom=ok');identify.assert_called_once_with('test-code')
                cookie=response['headers']['Set-Cookie'].split(';')[0]
                boot=self.call('/api/bootstrap',cookie=cookie)['json']
                self.assertEqual(boot['current_judge']['id'],'demo-judge-1')
                self.assertEqual(boot['entry']['wecom_userid'],'person1')
                self.assertEqual(self.call('/api/scores',cookie=cookie)['json']['scores'][0]['value'],23)
                replay=self.call(callback,cookie=browser)
                self.assertEqual(replay['headers']['Location'],'/?wecom=state');identify.assert_called_once()
            self.call('/api/admin/entry-mode','POST',{'mode':'general'},admin=True)
            self.assertEqual(self.call('/api/judge/select','POST',{'judge_id':'demo-judge-2'},cookie=cookie)['status'],403)
            duplicate=self.call('/api/admin/judges','POST',{'id':'demo-judge-2','name':'wrong','wecom_userid':'person1'},admin=True)
            self.assertEqual(duplicate['status'],400)
            self.call('/api/admin/judges','POST',{'id':'demo-judge-1','name':'演示评委1','active':False},admin=True)
            self.assertEqual(self.call('/api/scores',cookie=cookie)['status'],401)

    def test_wecom_unbound_member_expiry_and_errors(self):
        config={'WECOM_CORP_ID':'test-corp','WECOM_AGENT_ID':'1','WECOM_SECRET':'fake-secret','WECOM_PUBLIC_URL':'http://score.example.com'}
        with patch.dict(os.environ,config):
            start=self.call('/api/wecom/start',judge_as='');browser=start['headers']['Set-Cookie'].split(';')[0]
            state=parse_qs(urlparse(start['headers']['Location']).query)['state'][0]
            callback='/api/wecom/callback?state='+state+'&code=test'
            with app.connect() as db:db.execute('UPDATE wecom_states SET expires=0')
            with patch('wecom_auth.identify') as identify:
                self.assertEqual(self.call(callback,cookie=browser)['headers']['Location'],'/?wecom=state');identify.assert_not_called()
            start=self.call('/api/wecom/start',judge_as='');browser=start['headers']['Set-Cookie'].split(';')[0]
            state=parse_qs(urlparse(start['headers']['Location']).query)['state'][0]
            with patch('wecom_auth.identify',return_value=('test-corp','unbound-person')):
                response=self.call('/api/wecom/callback?state='+state+'&code=test',cookie=browser)
            cookie=response['headers']['Set-Cookie'].split(';')[0]
            self.assertIsNone(self.call('/api/bootstrap',cookie=cookie)['json']['current_judge'])
            self.assertEqual(self.call('/api/scores',cookie=cookie)['status'],401)
            self.call('/api/admin/entry-mode','POST',{'mode':'general'},admin=True)
            self.assertEqual(self.call('/api/judge/select','POST',{'judge_id':'demo-judge-3'},cookie=cookie)['status'],200)
            public=json.dumps(self.call('/api/bootstrap',judge_as='')['json'])
            self.assertNotIn('fake-secret',public);self.assertNotIn('unbound-person',public)

    def test_wecom_token_cache_retry_and_safe_errors(self):
        config={'WECOM_CORP_ID':'test-corp','WECOM_AGENT_ID':'1','WECOM_SECRET':'fake-secret','WECOM_PUBLIC_URL':'http://score.example.com'}
        with patch.dict(os.environ,config):
            app.wecom_auth._cache.clear()
            responses=[{'access_token':'token1','expires_in':7200},{'userid':'person1'},{'errcode':42001},{'access_token':'token2','expires_in':7200},{'UserId':'person2'}]
            with patch('wecom_auth._request',side_effect=responses) as request:
                self.assertEqual(app.wecom_auth.identify('code1'),('test-corp','person1'))
                self.assertEqual(app.wecom_auth.identify('code2'),('test-corp','person2'))
                self.assertEqual([call.args[0] for call in request.call_args_list],['gettoken','auth/getuserinfo','auth/getuserinfo','gettoken','auth/getuserinfo'])
            with patch('wecom_auth.urlopen',side_effect=RuntimeError('https://secret-example/?corpsecret=fake-secret')):
                with self.assertRaises(app.wecom_auth.WecomError) as error:app.wecom_auth._request('gettoken',{})
                self.assertEqual(str(error.exception),'network')
            with patch('wecom_auth._request',return_value={'openid':'external-person'}):
                with self.assertRaises(app.wecom_auth.WecomError):app.wecom_auth.identify('external-code')
            app.wecom_auth._cache.clear()

    def test_wecom_verification_upload_and_auth(self):
        filename='WW_verify_test123.txt';body={'filename':filename,'data':base64.b64encode(b'original-verification-content\n').decode()}
        self.assertEqual(self.call('/api/admin/wecom-verification','POST',body,judge_as='')['status'],401)
        self.assertEqual(self.call('/api/admin/wecom-verification','POST',body,admin=True)['status'],200)
        response=self.call('/'+filename,judge_as='');self.assertEqual(response['status'],200);self.assertEqual(response['raw'],b'original-verification-content\n')
        self.assertEqual(self.call('/api/admin/wecom-verification','POST',{**body,'filename':'../.env'},admin=True)['status'],400)
        self.assertEqual(self.call('/api/admin/entry-mode','POST',{'mode':'general'},judge_as='')['status'],401)

    def new_competition(self):
        response=self.call('/api/admin/competitions','POST',{'action':'create','title':'通用测试比赛','copy_rules':False},admin=True)
        self.assertEqual(response['status'],200)
        identifier=response['json']['id']
        self.assertEqual(self.call('/api/admin/competitions','POST',{'action':'switch','id':identifier},admin=True)['status'],200)
        return identifier

    def generic_rules(self):
        return [{'id':'creative','name':'创意方案','final_enabled':True,'dimensions':[{'name':'内容表现','max':60,'description':'内容及展示','bands':[],
            'subitems':[{'name':'内容完整','max':20,'definition':'内容完整性','elements':['目标清楚','背景完整','过程完整','成果完整','结论完整'],'scoring_rule':'每条要素参考4分'},
                        {'name':'方案价值','max':40,'definition':'方案的实际价值','elements':['价值明确','结果准确','落地可行','方法合理','表达清晰'],'scoring_rule':'每条要素参考8分'}]}]}]

    def test_competition_isolation_stale_writes_and_shared_admin(self):
        self.score(0,23)
        identifier=self.new_competition()
        state=self.call('/api/admin/state',admin=True)['json']
        self.assertEqual(state['competition_id'],identifier);self.assertEqual(state['projects'],[]);self.assertEqual(state['judges'],[])
        self.assertEqual(len(state['categories']),1);self.assertEqual(state['settings']['final_enabled'],'0')
        self.assertEqual(self.call('/api/admin/projects','POST',{'category':'general','number':1,'title':'新项目','person':'新人','department':'新部门'},admin=True)['status'],409)
        self.assertEqual(self.score(0,10)['status'],409)
        self.assertEqual(self.call('/api/admin/projects','POST',{'competition_id':identifier,'category':'general','number':1,'title':'新项目','person':'新人','department':'新部门'},admin=True)['status'],200)
        self.assertEqual(self.call('/api/admin/competitions','POST',{'competition_id':identifier,'action':'switch','id':'default'},admin=True)['status'],200)
        self.assertEqual(self.call('/api/scores')['json']['scores'][0]['value'],23)
        self.assertEqual(len(self.call('/api/admin/state',admin=True)['json']['projects']),6)
        self.assertEqual(self.call('/api/admin/projects','POST',{'competition_id':identifier,'category':'prompt','number':3,'title':'旧页误写','person':'测试','department':'测试'},admin=True)['status'],409)

    def test_configurable_scoring_totals_export_and_structure_lock(self):
        identifier=self.new_competition();rules=self.generic_rules()
        def post(path,body):return self.call(path,'POST',{'competition_id':identifier,**body},admin=True)
        self.assertEqual(post('/api/admin/categories',{'categories':rules})['status'],200)
        state=self.call('/api/admin/state',admin=True)['json'];version=state['rules_version']
        created=post('/api/admin/projects',{'category':'creative','number':1,'title':'通用项目','person':'参赛人','department':'业务部'})
        pid=created['json']['id'];jid=post('/api/admin/judges',{'name':'通用评委'})['json']['id']
        token=post('/api/admin/judge-entry',{'judge_id':jid})['json']['token']
        cookie=self.call('/api/judge/enter','POST',{'token':token},judge_as='')['headers']['Set-Cookie'].split(';')[0]
        for sub,value in enumerate((20,30)):
            body={'competition_id':identifier,'rules_version':version,'project_id':pid,'dimension':0,'subdimension':sub,'value':value}
            self.assertEqual(self.call('/api/score','PUT',body,cookie=cookie)['status'],200)
        board=self.call('/api/leaderboard',judge_as='')['json'];self.assertEqual(board['categories'][0]['name'],'创意方案');self.assertEqual(board['categories'][0]['projects'][0]['score'],50)
        report=self.call('/api/admin/export',admin=True)
        self.assertEqual(report['status'],200)
        with zipfile.ZipFile(io.BytesIO(report['raw'])) as archive:self.assertIn('总分（60分）',archive.read('xl/worksheets/sheet1.xml').decode())
        template=self.call('/api/admin/project-template?category=creative',admin=True)
        self.assertEqual(template['status'],200)
        with zipfile.ZipFile(io.BytesIO(template['raw'])) as archive:self.assertIn('创意方案',archive.read('xl/workbook.xml').decode())
        changed=json.loads(json.dumps(rules));changed[0]['dimensions'][0]['subitems'][0]['max']=10;changed[0]['dimensions'][0]['max']=50
        self.assertEqual(post('/api/admin/categories',{'categories':changed})['status'],400)
        rules[0]['dimensions'][0]['subitems'][0]['definition']='文字修订不改分值'
        self.assertEqual(post('/api/admin/categories',{'categories':rules})['status'],200)
        self.assertEqual(self.call('/api/scores',cookie=cookie)['json']['scores'][0]['value'],50)
        self.assertEqual(post('/api/admin/categories',{'categories':[]})['status'],400)

    def test_generic_final_weights_and_maximum(self):
        identifier=self.new_competition()
        def post(path,body):return self.call(path,'POST',{'competition_id':identifier,**body},admin=True)
        self.assertEqual(post('/api/admin/categories',{'categories':self.generic_rules()})['status'],200)
        self.assertEqual(post('/api/admin/competition-settings',{'title':'通用赛制','subtitle':'测试','final_enabled':True,'preliminary_weight':20,'final_weight':80})['status'],200)
        pid=post('/api/admin/projects',{'category':'creative','number':1,'title':'方案','person':'人员','department':'部门'})['json']['id']
        self.assertEqual(post('/api/admin/finalists',{'project_id':pid,'preliminary_score':61})['status'],400)
        self.assertEqual(post('/api/admin/finalists',{'project_id':pid,'preliminary_score':40})['status'],200)
        self.assertEqual(post('/api/admin/round',{'round':'final'})['status'],200)
        jid=post('/api/admin/judges',{'name':'评委'})['json']['id'];token=post('/api/admin/judge-entry',{'judge_id':jid})['json']['token']
        cookie=self.call('/api/judge/enter','POST',{'token':token},judge_as='')['headers']['Set-Cookie'].split(';')[0]
        state=self.call('/api/bootstrap',cookie=cookie)['json'];self.assertEqual([c['id'] for c in state['categories']],['creative'])
        for sub,value in enumerate((20,30)):
            self.assertEqual(self.call('/api/score','PUT',{'competition_id':identifier,'rules_version':state['rules_version'],'round':'final','project_id':pid,'dimension':0,'subdimension':sub,'value':value},cookie=cookie)['status'],200)
        self.assertEqual(self.call('/api/leaderboard')['json']['categories'][0]['projects'][0]['score'],48)
        self.assertEqual(post('/api/admin/competition-settings',{'title':'修改','final_enabled':False,'preliminary_weight':70,'final_weight':30})['status'],400)

    def test_complete_backup_contains_current_and_historical_competitions(self):
        self.score(0,23);identifier=self.new_competition()
        created=self.call('/api/admin/projects','POST',{'competition_id':identifier,'category':'general','number':1,'title':'备份测试项目','person':'人员','department':'部门'},admin=True)
        self.assertEqual(created['status'],200)
        with tempfile.TemporaryDirectory() as destination:
            result=subprocess.run([sys.executable,str(app.ROOT/'backup.py'),destination],env={**os.environ,'DATA_DIR':str(app.DATA)},capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            backup=next(Path(destination).iterdir())
            import sqlite3
            with closing(sqlite3.connect(backup/'competitions.sqlite3')) as registry:self.assertEqual(registry.execute("SELECT value FROM control WHERE key='active'").fetchone()[0],identifier)
            with closing(sqlite3.connect(backup/'competitions'/(identifier+'.sqlite3'))) as data:self.assertEqual(data.execute('SELECT title FROM projects').fetchone()[0],'备份测试项目')
            with closing(sqlite3.connect(backup/'scores.sqlite3')) as data:self.assertEqual(data.execute('SELECT value FROM scores').fetchone()[0],23)

    def test_poster_validation_and_path_protection(self):
        self.login()
        self.assertEqual(self.call('/api/admin/poster','POST',{'data':'PHNjcmlwdD4='},admin=True)['status'],400)
        self.assertEqual(self.call('/static/../app.py')['status'],404)
        self.assertEqual(self.call('/data/scores.sqlite3')['status'],404)


if __name__=='__main__':unittest.main()
