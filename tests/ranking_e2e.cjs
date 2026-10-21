// Configurable module selection and scoring sources, isolated from real contest data.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const {spawn}=require('child_process'),fs=require('fs'),os=require('os'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'ai-ranking-')),origin='http://127.0.0.1:8016';
const server=spawn(process.env.TEST_PYTHON||'python',['app.py'],{cwd:root,env:{...process.env,HOST:'127.0.0.1',PORT:'8016',DATA_DIR:temp,ADMIN_PASSWORD:'ranking-ui-test',COOKIE_SECURE:'0'}});
let browser;
(async()=>{
for(let i=0;i<60;i++){try{const r=await fetch(origin+'/api/bootstrap');await r.arrayBuffer();if(r.ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
browser=await chromium.launch({channel:'msedge',headless:true});const errors=[];
const admin=await browser.newPage({viewport:{width:1440,height:1000}});admin.on('pageerror',e=>errors.push(e.message));
await admin.goto(origin+'/admin');await admin.fill('#password','ranking-ui-test');await admin.click('#login-form button');await admin.click('[data-tab="finals"]');
const openSettings=async()=>{if(!await admin.locator('#ranking-settings-details').evaluate(el=>el.open))await admin.click('#ranking-settings-details summary')};
await openSettings();await admin.check('[name="category"][value="prompt"]');await admin.selectOption('#ranking-mode','weighted');await admin.fill('[name="preliminary_weight"]','60');await admin.fill('[name="final_weight"]','40');await Promise.all([admin.waitForResponse(r=>r.url().endsWith('/api/admin/state')),admin.click('#ranking-settings-form button.primary')]);
await admin.click('[data-ranking-category="prompt"]');await admin.click('[data-ranking-project="demo-project-1"]');
await admin.check('#ranking-project-form [name="included"]');await admin.selectOption('[name="initial_source"]','manual');await admin.fill('[name="initial_manual"]','80');await admin.selectOption('[name="final_source"]','manual');await admin.fill('[name="final_manual"]','90');await admin.click('#ranking-project-form button.primary');await admin.waitForSelector('#ranking-project-form',{state:'detached'});
const board=await browser.newPage({viewport:{width:1440,height:900}});board.on('pageerror',e=>errors.push(e.message));await board.goto(origin+'/leaderboard#prompt');await board.waitForSelector('#tab-prompt');
const score=()=>board.locator('[data-project="demo-project-1"] .score');assert.equal(await score().textContent(),'84.00');assert.equal(await board.locator('.ranking-group:visible').count(),1);
await admin.click('[data-ranking-project="demo-project-1"]');await admin.selectOption('[name="result_source"]','manual');await admin.fill('[name="result_manual"]','95.25');await admin.click('#ranking-project-form button.primary');await admin.waitForSelector('#ranking-project-form',{state:'detached'});
await board.waitForFunction(()=>document.querySelector('[data-project="demo-project-1"] .score')?.textContent==='95.25');
await admin.click('[data-ranking-project="demo-project-1"]');await admin.selectOption('[name="result_source"]','computed');await admin.selectOption('[name="initial_source"]','system');await admin.selectOption('[name="final_source"]','system');await admin.click('#ranking-project-form button.primary');await admin.waitForSelector('#ranking-project-form',{state:'detached'});
await openSettings();await admin.selectOption('#ranking-mode','preliminary');await Promise.all([admin.waitForResponse(r=>r.url().endsWith('/api/admin/state')),admin.click('#ranking-settings-form button.primary')]);
await board.waitForFunction(()=>document.querySelector('[data-project="demo-project-1"] .score')?.textContent==='待评分');
const entry=await admin.request.post(origin+'/api/admin/judge-entry',{data:{judge_id:'demo-judge-1'}});assert(entry.ok());assert((await admin.request.post(origin+'/api/judge/enter',{data:{token:(await entry.json()).token}})).ok());
for(const judge_id of ['demo-judge-1','demo-judge-2','demo-judge-3']){
 const access=await admin.request.post(origin+'/api/admin/judge-entry',{data:{judge_id}});assert(access.ok());assert((await admin.request.post(origin+'/api/judge/enter',{data:{token:(await access.json()).token}})).ok());
 for(const [dimension,value] of [20,20,15,15,10].entries())assert((await admin.request.put(origin+'/api/score',{data:{project_id:'demo-project-1',dimension,value}})).ok());
 const current=await (await admin.request.get(origin+'/api/leaderboard')).json();const row=current.categories.flatMap(c=>c.projects).find(p=>p.id==='demo-project-1');assert.equal(row.score,judge_id==='demo-judge-3'?80:null);
}
await board.waitForFunction(()=>document.querySelector('[data-project="demo-project-1"] .score')?.textContent==='80.00');
await admin.setViewportSize({width:390,height:844});await admin.click('[data-ranking-project="demo-project-1"]');
assert.equal(await admin.locator('[name="initial_manual"]').inputValue(),'80');assert.equal(await admin.locator('[name="final_manual"]').inputValue(),'90');
await admin.selectOption('[name="initial_source"]','manual');await admin.selectOption('[name="final_source"]','manual');
assert.equal(await admin.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
await admin.screenshot({path:path.join(root,'test-artifacts','ranking-sources-mobile.png'),fullPage:true});await admin.click('#ranking-project-form button.primary');await admin.waitForSelector('#ranking-project-form',{state:'detached'});
await admin.screenshot({path:path.join(root,'test-artifacts','ranking-config-mobile.png'),fullPage:true});
await openSettings();for(const module of ['prompt','agent','solution'])await admin.uncheck(`[name="category"][value="${module}"]`);
await Promise.all([admin.waitForResponse(r=>r.url().endsWith('/api/admin/state')),admin.click('#ranking-settings-form button.primary')]);
await board.getByText('暂无开放的排行榜模块').waitFor();
assert.equal((await admin.request.get(origin+'/api/admin/ranking-export')).status(),200);
assert.deepEqual(errors,[]);console.log('PASS: optional prompt module, manual stage scores, weighted score, final override, live automatic judging, retained manual values, mobile layout, no selected modules and Excel export.');
})().catch(error=>{console.error(error);process.exitCode=1}).finally(async()=>{if(browser)await browser.close();server.kill();setTimeout(()=>fs.rmSync(temp,{recursive:true,force:true}),500)});
