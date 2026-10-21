'use strict';
let presentationTimer,presentationBusy=false;
function currentPresentation(){return data?.presentation?.round===scoringRound&&(!data.presentation.competition_id||data.presentation.competition_id===data.competition_id)?data.presentation.project:null}
function applyPresentation(){
const current=currentPresentation(),slot=document.querySelector('#presentation-banner');
document.querySelectorAll('[data-project]').forEach(card=>{const active=card.dataset.project===current?.id;card.classList.toggle('is-presenting',active);let badge=card.querySelector('.presenting-label');if(active&&!badge){badge=document.createElement('span');badge.className='presenting-label';badge.textContent='正在宣讲';card.querySelector('.card-top')?.append(badge)}else if(!active)badge?.remove()});
document.querySelectorAll('[data-cat]').forEach(button=>button.classList.toggle('has-presentation',button.dataset.cat===current?.category));
document.querySelector('.project-info')?.classList.remove('is-presenting');
if(!slot)return;
if(data.presentation?.competition_id&&data.presentation.competition_id!==data.competition_id||data.presentation?.round&&data.presentation.round!==scoringRound){slot.innerHTML='<section class="live-presentation phase-changed"><strong>比赛或评分阶段已切换，请刷新页面后继续</strong><button class="secondary" id="reload-scoring-round">刷新页面</button></section>';document.querySelector('#reload-scoring-round').onclick=()=>location.reload();return}
slot.innerHTML='';
}
async function pollPresentation(){
clearTimeout(presentationTimer);if(presentationBusy)return;if(!judge||!data)return;presentationBusy=true;let keep=true;
try{const latest=await api('/api/presentation');const changed=JSON.stringify(latest)!==JSON.stringify(data.presentation);data.presentation=latest;document.querySelector('#presentation-banner')?.classList.remove('presentation-stale');if(changed){if(location.hash==='#projects'&&latest.competition_id===data.competition_id)list();else applyPresentation()}}
catch(error){if(error.status===401){keep=false;try{const latest=await api('/api/bootstrap');if(latest.competition_id!==data.competition_id){data.presentation={competition_id:latest.competition_id,round:latest.round,project:null};applyPresentation()}}catch{}}document.querySelector('#presentation-banner')?.classList.add('presentation-stale')}
finally{presentationBusy=false;if(keep)presentationTimer=setTimeout(pollPresentation,3000)}
}
