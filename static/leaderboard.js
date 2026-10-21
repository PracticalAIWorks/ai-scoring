'use strict';
const $=s=>document.querySelector(s),esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let modules=['agent','solution'],names={},eventTitle='比赛评审',eventId='';
let timer,busy=false,signature='',lastSuccess,selected=modules.includes(location.hash.slice(1))?location.hash.slice(1):'agent';
function selectCategory(id){
selected=modules.includes(id)?id:(modules[0]||'');
document.querySelectorAll('[data-category-tab]').forEach(button=>{const active=button.dataset.categoryTab===selected;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1});
document.querySelectorAll('.ranking-group').forEach(panel=>panel.hidden=panel.dataset.category!==selected);
document.title=(names[selected]||eventTitle)+' · 实时排行榜';
window.RankingScroller.activate(eventId+':'+selected,document.querySelector('.ranking-group:not([hidden]) .ranking-viewport'));
}
function render(data){
window.RankingScroller.configure(data.scroll_settings);window.RankingScroller.capture();eventId=data.competition_id||'';
modules=data.categories.map(c=>c.id);names=Object.fromEntries(data.categories.map(c=>[c.id,c.name]));eventTitle=data.title||'比赛评审';if(!modules.includes(selected))selected=modules[0]||'';const next=JSON.stringify([data.competition_id,data.title,data.round,data.score_caption,data.categories]);
if(next!==signature){
signature=next;
$('.board-brand h1').textContent=eventTitle;$('.board-brand .eyebrow').textContent='COMPETITION RANKINGS';$('.footer-label').textContent=eventTitle;
$('.category-tabs').innerHTML=data.categories.map((group,i)=>`<button id="tab-${esc(group.id)}" role="tab" aria-controls="panel-${esc(group.id)}" aria-selected="${group.id===selected}" data-category-tab="${esc(group.id)}">${esc(group.name)}<span class="tab-marker" aria-hidden="true">${String(i+1).padStart(2,'0')}</span></button>`).join('');bindTabs();
$('#rankings').innerHTML=data.categories.map(group=>`<section class="ranking-group" id="panel-${esc(group.id)}" role="tabpanel" aria-labelledby="tab-${esc(group.id)}" data-category="${esc(group.id)}" ${group.id===selected?'':'hidden'}><header class="module-heading"><div><p class="module-caption">${esc(data.score_caption||(data.round==='final'?'复试综合成绩':'评分平均成绩'))} · 实时排行榜</p><h2>${esc(group.name)}</h2></div><div class="project-count"><strong>${String(group.projects.length).padStart(2,'0')}</strong><span>参评项目</span></div></header><div class="ranking-table"><div class="ranking-row table-head" aria-hidden="true"><span>排名</span><span>项目主题</span><span>姓名</span><span>部门</span><span>最终分数</span></div><div class="ranking-viewport" tabindex="0" role="region" aria-label="${esc(group.name)}项目排名，可手动滚动"><div class="ranking-list">${group.projects.map(row=>`<div class="ranking-row ${row.rank&&row.rank<=3?'top-rank podium-'+row.rank:''} ${row.score===null?'pending-row':''}" data-project="${esc(row.id)}"><span class="rank ${row.rank&&row.rank<=3?'medal medal-'+row.rank:''}" aria-label="${row.rank?'第'+row.rank+'名':'暂无排名'}">${row.rank??'—'}</span><strong class="project-title">${esc(row.title)}</strong><span class="person">${esc(row.person)}</span><span class="department">${esc(row.department)}</span><strong class="score">${row.score===null?'待评分':Number(row.score).toFixed(2)}</strong></div>`).join('')}${group.projects.length?'':'<div class="empty"><span aria-hidden="true">—</span><p>暂无参评项目</p></div>'}</div></div></div></section>`).join('')+(data.categories.length?'':'<div class="empty"><p>暂无开放的排行榜模块</p></div>');
selectCategory(selected);
}
lastSuccess=new Date();$('#connection').className='connected';$('#connection span').textContent='实时更新中';$('#updated').textContent='更新于 '+lastSuccess.toLocaleTimeString('zh-CN',{hour12:false});
}
async function refresh(){if(busy)return;busy=true;clearTimeout(timer);try{const r=await fetch('/api/leaderboard',{cache:'no-store',signal:AbortSignal.timeout(8000)});if(!r.ok)throw new Error('读取失败');render(await r.json())}catch{$('#connection').className='disconnected';$('#connection span').textContent='连接中断，正在重试';$('#updated').textContent=lastSuccess?'最近更新 '+lastSuccess.toLocaleTimeString('zh-CN',{hour12:false}):'暂时无法连接评分服务'}finally{busy=false;timer=setTimeout(refresh,3000)}}
function bindTabs(){document.querySelectorAll('[data-category-tab]').forEach(button=>{
button.onclick=()=>{const id=button.dataset.categoryTab;selectCategory(id);if(location.hash!=='#'+id)location.hash=id};
button.onkeydown=event=>{let index=modules.indexOf(selected);if(event.key==='ArrowRight')index=(index+1)%modules.length;else if(event.key==='ArrowLeft')index=(index+modules.length-1)%modules.length;else if(event.key==='Home')index=0;else if(event.key==='End')index=modules.length-1;else return;event.preventDefault();document.querySelector('[data-category-tab="'+modules[index]+'"]').click();document.querySelector('[data-category-tab="'+modules[index]+'"]').focus()};
});
}
window.addEventListener('hashchange',()=>selectCategory(location.hash.slice(1)));
$('#fullscreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen()}catch{$('#fullscreen span').textContent='可按 F11 全屏'}};
document.addEventListener('fullscreenchange',()=>$('#fullscreen span').textContent=document.fullscreenElement?'退出全屏':'全屏展示');
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});window.addEventListener('online',refresh);selectCategory(selected);refresh();
