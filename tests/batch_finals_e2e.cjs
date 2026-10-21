// Real browser coverage for per-module checkboxes and default automatic scoring.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const {spawn}=require('child_process'),fs=require('fs'),os=require('os'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'ai-batch-')),origin='http://127.0.0.1:8018';
const server=spawn(process.env.TEST_PYTHON||'python',['app.py'],{cwd:root,env:{...process.env,HOST:'127.0.0.1',PORT:'8018',DATA_DIR:temp,ADMIN_PASSWORD:'batch-ui-test',COOKIE_SECURE:'0'}});
let browser;
(async()=>{
for(let i=0;i<60;i++){try{const r=await fetch(origin+'/api/bootstrap');await r.arrayBuffer();if(r.ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
browser=await chromium.launch({channel:'msedge',headless:true});const errors=[];
const admin=await browser.newPage({viewport:{width:1440,height:1000}});admin.on('pageerror',e=>errors.push(e.message));
await admin.goto(origin+'/admin');await admin.fill('#password','batch-ui-test');await admin.click('#login-form button');await admin.click('[data-tab="finals"]');
await admin.click('[data-finalist-category="prompt"]');assert.equal(await admin.locator('[data-qual-row]').count(),2);
await admin.click('[data-finalist="demo-project-1"]');assert.equal(await admin.inputValue('#finalist-initial-source'),'system');assert(await admin.locator('#preliminary-score').isHidden());await admin.click('#finalist-form [data-close]');
const batch=async(kind,action)=>{await Promise.all([admin.waitForResponse(r=>r.url().endsWith('/api/admin/state')),admin.click(`[data-${kind}-action="${action}"]`)]);await admin.waitForFunction(kind=>document.querySelector('#'+kind+'-selected')?.textContent==='已选0项',kind)};
await admin.check('#qual-select-all');assert.equal(await admin.locator('[data-qual-check]:checked').count(),2);await batch('qual','finalists_add');
let state=await (await admin.request.get(origin+'/api/admin/state')).json();assert(state.final_categories.includes('prompt'));assert.equal(state.round,'preliminary');assert.equal(state.finalists.filter(f=>f.active).length,2);
for(const id of ['demo-project-1','demo-project-2']){const c=state.ranking_projects.find(c=>c.project_id===id);assert.deepEqual([c.initial_source,c.final_source,c.result_source,c.included],['system','system','computed',1])}
await admin.click('[data-finalist-category="solution"]');assert.equal(await admin.locator('[data-qual-check]:checked').count(),0);
await admin.fill('#qual-search','从数据');await admin.check('#qual-select-all');assert.equal(await admin.locator('[data-qual-check]:checked').count(),1);await admin.uncheck('#qual-include-ranking');await batch('qual','finalists_add');
state=await (await admin.request.get(origin+'/api/admin/state')).json();assert.equal(state.finalists.filter(f=>f.active).length,3);assert.equal(state.ranking_projects.find(c=>c.project_id==='demo-project-3').included,0);
await admin.click('[data-ranking-category="prompt"]');await admin.check('#rank-select-all');await batch('rank','ranking_remove');
state=await (await admin.request.get(origin+'/api/admin/state')).json();assert.equal(state.finalists.filter(f=>f.active).length,3);assert.equal(state.ranking_projects.find(c=>c.project_id==='demo-project-1').included,0);
await admin.check('#rank-select-all');await batch('rank','ranking_add');
await admin.click('[data-finalist-category="prompt"]');await admin.fill('#qual-search','客户');await admin.check('#qual-select-all');assert.equal(await admin.locator('[data-qual-check]:checked').count(),1);
await batch('qual','finalists_remove');state=await (await admin.request.get(origin+'/api/admin/state')).json();assert.equal(state.finalists.find(f=>f.project_id==='demo-project-2').active,0);assert.equal(state.ranking_projects.find(c=>c.project_id==='demo-project-2').included,1);
await admin.setViewportSize({width:390,height:844});assert(await admin.locator('#rank-select-all').isVisible());assert(await admin.locator('#qual-select-all').isVisible());
await admin.click('[data-ranking-category="solution"]');await admin.check('[data-rank-check="demo-project-3"]');await batch('rank','ranking_add');assert(await admin.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
await admin.screenshot({path:path.join(root,'test-artifacts','batch-finals-mobile.png'),fullPage:true});await admin.setViewportSize({width:1440,height:1000});await admin.evaluate(()=>window.scrollTo(0,0));await admin.waitForTimeout(100);await admin.screenshot({path:path.join(root,'test-artifacts','batch-finals-desktop.png'),fullPage:true});
// Only eligible projects appear after changing the judging stage.
await admin.selectOption('#scoring-round','final');await admin.click('#save-scoring-round');await admin.click('#confirm-round');await admin.waitForSelector('#confirm-round',{state:'detached'});
const access=await admin.request.post(origin+'/api/admin/judge-entry',{data:{judge_id:'demo-judge-1'}});assert(access.ok());assert((await admin.request.post(origin+'/api/judge/enter',{data:{token:(await access.json()).token}})).ok());
const bootstrap=await (await admin.request.get(origin+'/api/bootstrap')).json();assert(bootstrap.categories.some(c=>c.id==='prompt'));assert(bootstrap.projects.some(p=>p.id==='demo-project-1'));assert(!bootstrap.projects.some(p=>p.id==='demo-project-2'));
assert.deepEqual(errors,[]);console.log('PASS: prompt final qualification, per-module search/select-all, automatic score defaults, bulk enrollment/ranking/removal, independent lists, mobile checkboxes and final-stage access.');
})().catch(error=>{console.error(error);process.exitCode=1}).finally(async()=>{if(browser)await browser.close();server.kill();setTimeout(()=>fs.rmSync(temp,{recursive:true,force:true}),500)});
