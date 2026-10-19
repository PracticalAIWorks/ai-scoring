// Optional browser checks. Requires Playwright and an installed Edge browser.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const {spawn}=require('child_process');
const fs=require('fs'),os=require('os'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'ai-scoring-e2e-'));
const server=spawn(process.env.TEST_PYTHON||'python',['app.py'],{cwd:root,env:{...process.env,PORT:'8011',DATA_DIR:temp,ADMIN_PASSWORD:'ui-test-password'}});
let browser;
(async()=>{
for(let i=0;i<50;i++){try{if((await fetch('http://127.0.0.1:8011/api/bootstrap')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
browser=await chromium.launch({channel:'msedge',headless:true});
const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
const p=await context.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));
const shot=async name=>{fs.mkdirSync(path.join(root,'test-artifacts'),{recursive:true});await p.screenshot({path:path.join(root,'test-artifacts',name+'.png'),fullPage:true})};
await p.goto('http://127.0.0.1:8011/');await p.waitForSelector('#judge-select');await shot('mobile-home');
await p.selectOption('#judge-select','demo-judge-1');await p.click('#enter');await p.waitForSelector('.project-card');
await p.click('[data-project="demo-project-1"]');await p.waitForSelector('.score-input');
for(const [k,v] of [25,25,20,20,10].entries())await p.locator(`[data-dim="${k}"]`).fill(String(v));
await p.locator('#save-status').filter({hasText:'已保存到服务器'}).waitFor();assert.equal(await p.locator('#score-total').textContent(),'100');
await shot('mobile-score');await p.click('#back-list');await p.waitForSelector('.project-card.done');
await shot('mobile-projects');await p.reload();await p.waitForSelector('.project-card.done');
await p.click('[data-project="demo-project-1"]');assert.equal(await p.locator('[data-dim="0"]').inputValue(),'25');
await p.locator('[data-dim="0"]').fill('26');await p.locator('#err-0').filter({hasText:'请输入'}).waitFor();
await p.locator('[data-dim="0"]').fill('0');await p.locator('#save-status').filter({hasText:'已保存到服务器'}).waitFor();assert.equal(await p.locator('#score-total').textContent(),'75');
await context.setOffline(true);await p.locator('[data-dim="0"]').fill('18.25');await p.waitForTimeout(800);assert.notEqual(await p.locator('#save-status').textContent(),'已保存到服务器');
assert(Object.keys(await p.evaluate(()=>JSON.parse(localStorage.getItem('ai-pending')))).length>0);
await context.setOffline(false);await p.reload();await p.locator('#save-status').filter({hasText:'已保存到服务器'}).waitFor();assert.equal(await p.locator('[data-dim="0"]').inputValue(),'18.25');
// Immediate exit must retain/flush the latest field, including clear-to-blank.
await p.locator('[data-dim="1"]').fill('');await p.click('#back-list');await p.reload();await p.waitForSelector('[data-project="demo-project-1"]');await p.click('[data-project="demo-project-1"]');assert.equal(await p.locator('[data-dim="1"]').inputValue(),'');
await p.click('#back-list');await p.click('#switch-judge');await p.selectOption('#judge-select','demo-judge-2');await p.click('#enter');await p.waitForSelector('.project-card');assert.equal(await p.locator('.project-card.done').count(),0);
await p.click('[data-cat="solution"]');await p.click('[data-project="demo-project-3"]');assert.equal(await p.locator('.score-input').count(),4);
await p.click('#back-list');await p.click('[data-cat="agent"]');await p.click('[data-project="demo-project-5"]');assert.equal(await p.locator('.score-input').count(),5);
for(const width of [320,360,390,768,1440]){await p.setViewportSize({width,height:900});assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`horizontal overflow at ${width}`)}
await p.goto('http://127.0.0.1:8011/admin');await p.fill('#password','ui-test-password');await p.click('#login-form button');await p.waitForSelector('#export');
const downloadEvent=p.waitForEvent('download');await p.click('#export');const download=await downloadEvent;await download.saveAs(path.join(temp,'export.xlsx'));assert(fs.statSync(path.join(temp,'export.xlsx')).size>2000);
await shot('admin-overview');await p.click('[data-tab="projects"]');await p.click('#add-project');await p.fill('[name="title"]','正式项目测试');await p.fill('[name="person"]','测试参赛人');await p.fill('[name="department"]','测试部门');await p.click('#project-form button.primary');await p.waitForSelector('.modal',{state:'detached'});await p.getByRole('cell',{name:'正式项目测试',exact:true}).waitFor();
await p.click('[data-tab="judges"]');await p.click('#add-judge');await p.fill('#judge-form [name="name"]','新增评委测试');await p.click('#judge-form .primary');await p.waitForSelector('.modal',{state:'detached'});await p.getByText('新增评委测试',{exact:true}).waitFor();
await p.click('[data-tab="settings"]');await p.fill('[name="subtitle"]','正式文案测试');await p.click('#settings-form .primary');await p.waitForTimeout(400);assert.equal(await p.locator('[name="subtitle"]').inputValue(),'正式文案测试');
await p.setInputFiles('#poster-upload',path.join(root,'test-artifacts','mobile-home.png'));await p.waitForSelector('.poster-preview');
const publicPage=await context.newPage();await publicPage.goto('http://127.0.0.1:8011/');await publicPage.waitForSelector('body.has-poster');assert(await publicPage.evaluate(()=>getComputedStyle(document.body).getPropertyValue('--competition-poster').includes('/media/')));await publicPage.screenshot({path:path.join(root,'test-artifacts','poster-background.png'),fullPage:true});await publicPage.selectOption('#judge-select','demo-judge-1');await publicPage.click('#enter');await publicPage.waitForSelector('.project-card');assert(await publicPage.locator('body').evaluate(b=>b.classList.contains('has-poster')));await publicPage.click('[data-cat="prompt"]');await publicPage.click('[data-project="demo-project-1"]');await publicPage.waitForSelector('.score-panel');assert(await publicPage.locator('body').evaluate(b=>b.classList.contains('has-poster')));await publicPage.close();
await p.click('#remove-poster');await p.waitForSelector('.poster-preview',{state:'detached'});
assert.deepEqual(errors,[]);console.log('PASS: mobile scoring, persistence, offline recovery, zero/clear, categories, responsive layout, admin CRUD and Excel download.');
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{if(browser)await browser.close();server.kill();setTimeout(()=>{fs.rmSync(temp,{recursive:true,force:true})},500)});
