const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const {spawn}=require('child_process'),fs=require('fs'),os=require('os'),path=require('path'),assert=require('assert');
const root=path.resolve(__dirname,'..'),temp=fs.mkdtempSync(path.join(os.tmpdir(),'ai-scroll-')),origin='http://127.0.0.1:8017';
const server=spawn(process.env.TEST_PYTHON||'python',['app.py'],{cwd:root,env:{...process.env,HOST:'127.0.0.1',PORT:'8017',DATA_DIR:temp,ADMIN_PASSWORD:'scroll-ui-test',COOKIE_SECURE:'0'}});
let browser;
(async()=>{
for(let i=0;i<60;i++){try{const r=await fetch(origin+'/api/bootstrap');await r.arrayBuffer();if(r.ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
browser=await chromium.launch({channel:'msedge',headless:true});const errors=[];
const admin=await browser.newPage({viewport:{width:1440,height:960}});admin.on('pageerror',e=>errors.push(e.message));
await admin.goto(origin+'/admin');await admin.fill('#password','scroll-ui-test');await admin.click('#login-form button');await admin.waitForSelector('#open-leaderboard');
const settings={categories:['agent','solution'],mode:'manual',preliminary_weight:70,final_weight:30,scroll_enabled:true,scroll_speed:16,scroll_top_hold:2,scroll_bottom_hold:1};
const post=async(url,data)=>{const r=await admin.request.post(origin+url,{data});assert(r.ok(),await r.text());return r.json()};
await post('/api/admin/ranking-settings',settings);
for(let i=1;i<=20;i++){
 const p=await post('/api/admin/projects',{category:'agent',number:i+10,title:'测试项目 '+String(i).padStart(2,'0')+' · 企业智能化应用',person:'测试选手'+i,department:'测试部门',intro:'',active:true});
 await post('/api/admin/ranking-project',{project_id:p.id,included:true,initial_source:'manual',initial_manual:80,final_source:'manual',final_manual:90,result_source:'manual',result_manual:100-i});
}
await admin.click('[data-tab="finals"]');await admin.click('#ranking-settings-details summary');await admin.waitForSelector('[name="scroll_speed"]');assert.equal(await admin.inputValue('[name="scroll_speed"]'),'16');
await admin.fill('[name="scroll_top_hold"]','2');await admin.fill('[name="scroll_bottom_hold"]','1');await Promise.all([admin.waitForResponse(r=>r.url().endsWith('/api/admin/state')),admin.click('#ranking-settings-form button.primary')]);
const board=await browser.newPage({viewport:{width:1440,height:900}});board.on('pageerror',e=>errors.push(e.message));
await board.clock.install();await board.goto(origin+'/leaderboard#agent');await board.waitForSelector('.ranking-list [data-project]');
const metric=()=>board.locator('#panel-agent .ranking-viewport').evaluate(el=>({top:el.scrollTop,max:el.scrollHeight-el.clientHeight,height:el.clientHeight,phase:el.dataset.scrollPhase}));
let m=await metric();assert(m.max>500);assert.equal(m.top,0);assert.equal(await board.locator('select,input,#scroll-toggle').count(),0);
await board.clock.runFor(1200);assert.equal((await metric()).top,0);
await board.clock.runFor(1100);m=await metric();assert(Math.abs(m.top-m.max)<2);assert.equal(m.phase,'bottom');
await board.clock.runFor(400);assert(Math.abs((await metric()).top-m.max)<2);
await board.clock.runFor(2400);let moving=await metric();assert(moving.top<moving.max-10&&moving.top>moving.max-65,JSON.stringify(moving));
// Refresh with changed scores must not reset the scrolling position or its hold timers.
const response=await admin.request.get(origin+'/api/leaderboard');let payload=await response.json();payload.categories.find(c=>c.id==='agent').projects[0].score=99.5;
await board.route('**/api/leaderboard',route=>route.fulfill({json:payload}));await board.evaluate(()=>refresh());
const refreshed=await metric();assert(Math.abs(refreshed.top-moving.top)<3);
await board.clock.runFor(1600);assert((await metric()).top<refreshed.top-18);
// Fast-forward the ascent; the first places must then stay still at the top.
await board.clock.runFor(Math.ceil((await metric()).top/16*1000)+150);
assert.equal((await metric()).top,0);await board.clock.runFor(900);assert.equal((await metric()).top,0);
await board.screenshot({path:path.join(root,'test-artifacts','leaderboard-scroll-desktop.png')});
// Backend switches apply without adding controls to the display.
payload.scroll_settings.scroll_enabled=false;await board.evaluate(()=>refresh());await board.clock.runFor(6000);assert.equal((await metric()).top,0);
for(const width of [320,390,768,1920]){await board.setViewportSize({width,height:width<640?844:1080});assert(await board.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),String(width));assert((await metric()).height>100)}
await board.setViewportSize({width:390,height:844});await board.screenshot({path:path.join(root,'test-artifacts','leaderboard-scroll-mobile.png')});
await board.click('#tab-solution');const empty=await board.locator('#panel-solution .ranking-viewport').evaluate(el=>el.scrollHeight<=el.clientHeight);assert(empty);
await board.click('#tab-agent');assert.equal((await metric()).top,0);
assert.deepEqual(errors,[]);console.log('PASS: 20-project viewport, fixed headings, slow upward cycle, bottom/top holds, polling without jumps, backend-only controls, live disable, responsive display and module reset.');
})().catch(error=>{console.error(error);process.exitCode=1}).finally(async()=>{if(browser)await browser.close();server.kill();setTimeout(()=>fs.rmSync(temp,{recursive:true,force:true}),500)});
