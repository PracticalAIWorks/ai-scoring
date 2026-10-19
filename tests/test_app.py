import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

TEMP = tempfile.TemporaryDirectory()
os.environ['DATA_DIR'] = TEMP.name
os.environ['ADMIN_PASSWORD'] = 'test-admin-password'
import app


class ScoringTest(unittest.TestCase):
    def setUp(self):
        with app.connect() as db:
            db.execute('DELETE FROM scores')
            db.execute('DELETE FROM write_versions')
            db.execute('DELETE FROM sessions')
            db.execute('UPDATE judges SET active=1')
            db.execute('UPDATE projects SET active=1')
        self.cookie = ''

    def call(self,path,method='GET',body=None,admin=False,origin=None):
        raw=json.dumps(body or {}).encode()
        url,_,query=path.partition('?')
        env={'PATH_INFO':url,'QUERY_STRING':query,'REQUEST_METHOD':method,'CONTENT_LENGTH':str(len(raw)),
             'wsgi.input':io.BytesIO(raw),'HTTP_HOST':'localhost:8000','REMOTE_ADDR':'127.0.0.1'}
        if admin:env['HTTP_COOKIE']=self.cookie
        if origin:env['HTTP_ORIGIN']=origin
        response={}
        def start(status,headers):response.update(status=int(status.split()[0]),headers=dict(headers))
        data=b''.join(app.application(env,start))
        response['raw']=data
        if response['headers']['Content-Type'].startswith('application/json'):response['json']=json.loads(data)
        return response

    def login(self):
        r=self.call('/api/admin/login','POST',{'password':'test-admin-password'})
        self.assertEqual(r['status'],200)
        self.cookie=r['headers']['Set-Cookie'].split(';')[0]

    def score(self,k,v,judge='demo-judge-1',project='demo-project-1'):
        return self.call('/api/score','PUT',{'judge_id':judge,'project_id':project,'dimension':k,'value':v})

    def test_public_no_login(self):
        self.assertEqual(self.call('/')['status'],200)
        self.assertEqual(self.call('/api/bootstrap')['status'],200)

    def test_rubric_weights(self):
        self.assertEqual([d['max'] for d in app.CATEGORIES['agent']['dimensions']],[25,25,25,20,5])
        self.assertEqual(len(app.CATEGORIES['solution']['dimensions']),4)

    def test_save_restore_and_update(self):
        self.assertEqual(self.score(0,20.5)['status'],200)
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
        for v in (-1,25.01,True,'20',1.234,float('nan'),float('inf')):
            self.assertEqual(self.score(0,v)['status'],400,msg=str(v))
        for k in (-1,5,True,'0'):
            self.assertEqual(self.score(k,10)['status'],400)

    def test_wrong_ids(self):
        self.assertEqual(self.score(0,10,judge='missing')['status'],400)
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

    def test_poster_validation_and_path_protection(self):
        self.login()
        self.assertEqual(self.call('/api/admin/poster','POST',{'data':'PHNjcmlwdD4='},admin=True)['status'],400)
        self.assertEqual(self.call('/static/../app.py')['status'],404)
        self.assertEqual(self.call('/data/scores.sqlite3')['status'],404)


if __name__=='__main__':unittest.main()
