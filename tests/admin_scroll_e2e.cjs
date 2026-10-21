// Wheel input must move the workspace immediately, without moving the page header.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const {spawn}=require('child_process'),fs=require('fs'),os=require('os'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'ai-admin-scroll-')),origin='http://127.0.0.1:8019';
const server=spawn(process.env.TEST_PYTHON||'python',['app.py'],{cwd:root,env:{...process.env,HOST:'127.0.0.1',PORT:'8019',DATA_DIR:temp,ADMIN_PASSWORD:'admin-scroll-test',COOKIE_SECURE:'0'}});
let browser;
(async()=>{
for(let i=0;i<60;i++){try{const r=await fetch(origin+'/api/bootstrap');await r.arrayBuffer();if(r.ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
browser=await chromium.launch({channel:'msedge',headless:true});const errors=[];
const page=await browser.newPage({viewport:{width:1440,height:1000}});page.on('pageerror',e=>errors.push(e.message));
await page.goto(origin+'/admin');await page.fill('#password','admin-scroll-test');await page.click('#login-form button');await page.waitForSelector('#open-leaderboard');
assert.equal(await page.locator('[data-overview-module]').count(),3);
assert.equal(await page.locator('#panel tbody tr').count(),2);
await page.click('[data-overview-module="solution"]');assert.equal(await page.locator('#panel tbody tr').count(),2);
assert((await page.locator('#panel tbody').innerText()).includes('从数据到决策'));
assert(!(await page.locator('#panel tbody').innerText()).includes('工作简报'));
await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/admin/state')),page.click('#refresh')]);
assert(await page.locator('[data-overview-module="solution"]').getAttribute('aria-pressed')==='true');
await page.setViewportSize({width:390,height:844});assert(await page.locator('[data-overview-module="agent"]').isVisible());await page.click('[data-overview-module="agent"]');assert.equal(await page.locator('#panel tbody tr').count(),2);
await page.setViewportSize({width:1440,height:1000});
for(let i=3;i<=24;i++){const r=await page.request.post(origin+'/api/admin/projects',{data:{category:'solution',number:i,title:'滚动检查项目'+i,person:'测试人员'+i,department:'测试部门',active:true}});assert(r.ok())}
await page.reload();await page.waitForSelector('#open-leaderboard');await page.click('[data-tab="projects"]');await page.click('[data-project-module="solution"]');assert.equal(await page.locator('#panel tbody tr').count(),24);
const metrics=()=>page.evaluate(()=>{const pane=document.querySelector('#panel'),head=document.querySelector('.page-heading'),nav=document.querySelector('#admin>nav');return {window:scrollY,top:pane.scrollTop,height:pane.clientHeight,max:pane.scrollHeight-pane.clientHeight,heading:head.getBoundingClientRect().top,nav:nav.getBoundingClientRect().top,overflow:document.documentElement.scrollWidth>innerWidth}});
const wheel=async delta=>{const pane=await page.locator('#panel').boundingBox();await page.mouse.move(pane.x+pane.width*.65,pane.y+pane.height*.65);await page.mouse.wheel(0,delta);await page.waitForTimeout(200)};
for(const [width,height] of [[1440,1000],[390,844],[320,640],[900,700]]){
 await page.setViewportSize({width,height});await page.locator('#panel').evaluate(el=>el.scrollTop=0);const before=await metrics();assert(before.height>100);assert(before.max>400);assert.equal(before.window,0);assert(!before.overflow);
 await wheel(750);const after=await metrics();assert(after.top>200,JSON.stringify(after));assert.equal(after.window,0);assert(Math.abs(after.heading-before.heading)<1);assert(Math.abs(after.nav-before.nav)<1);
 await wheel(100000);const bottom=await metrics();assert(bottom.top>bottom.max-2);await wheel(1500);assert.equal((await metrics()).window,0);
 if(width===1440)await page.screenshot({path:path.join(root,'test-artifacts','admin-scroll-desktop.png')});
 if(width===390)await page.screenshot({path:path.join(root,'test-artifacts','admin-scroll-mobile.png')});
}
await page.setViewportSize({width:1440,height:1000});
await page.locator('#panel').evaluate(el=>el.scrollTop=el.scrollHeight);
const toggle=page.locator('[data-toggle]').last();await toggle.scrollIntoViewIfNeeded();
let savedTop=await page.locator('#panel').evaluate(el=>el.scrollTop);
await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/admin/state')),toggle.click()]);
await page.waitForFunction(()=>document.querySelector('[data-toggle]:last-child')!==null);
assert(Math.abs(await page.locator('#panel').evaluate(el=>el.scrollTop)-savedTop)<2,'Project toggle must preserve scroll');
await page.locator('[data-edit]').last().click();await page.fill('#project-form [name="title"]','Updated project');
savedTop=await page.locator('#panel').evaluate(el=>el.scrollTop);
await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/admin/state')),page.click('#project-form button.primary')]);
await page.waitForSelector('#project-form',{state:'detached'});
assert(Math.abs(await page.locator('#panel').evaluate(el=>el.scrollTop)-savedTop)<2,'Project edit must preserve scroll');
for(let i=1;i<=20;i++){const r=await page.request.post(origin+'/api/admin/judges',{data:{name:'Scroll judge '+i,active:true}});assert(r.ok())}
await page.click('#refresh');await page.click('[data-tab="judges"]');assert.equal(await page.locator('#panel').evaluate(el=>el.scrollTop),0);
await page.locator('[data-toggle-judge]').last().scrollIntoViewIfNeeded();savedTop=await page.locator('#panel').evaluate(el=>el.scrollTop);assert(savedTop>200);
await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/admin/state')),page.locator('[data-toggle-judge]').last().click()]);
assert(Math.abs(await page.locator('#panel').evaluate(el=>el.scrollTop)-savedTop)<2,'Judge toggle must preserve scroll');
await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/admin/state')),page.click('#refresh')]);
assert(Math.abs(await page.locator('#panel').evaluate(el=>el.scrollTop)-savedTop)<2,'Refresh must preserve scroll');

await page.click('[data-tab="projects"]');await page.click('#add-project');await page.waitForSelector('#project-form');assert(await page.locator('#project-form [name="title"]').isVisible());await page.click('[data-close]');assert.equal((await metrics()).window,0);
assert.deepEqual(errors,[]);console.log('PASS: 24-project wheel scrolling moves only the workspace, fixed header/navigation, no scroll chaining, desktop/mobile/tablet sizing, tab reset and project dialog.');
})().catch(error=>{console.error(error);process.exitCode=1}).finally(async()=>{if(browser)await browser.close();server.kill();setTimeout(()=>fs.rmSync(temp,{recursive:true,force:true}),500)});
