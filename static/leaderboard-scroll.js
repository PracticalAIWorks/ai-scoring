'use strict';
// Keep animation independent from the three-second data refresh.
window.RankingScroller=(()=>{
  const defaults={speed:16,topHold:12,bottomHold:3,enabled:true};
  let preferences={...defaults};
  let viewport=null,key='',position=0,phase='top',remaining=preferences.topHold*1000,manualWait=0,last=0,paused=matchMedia('(prefers-reduced-motion: reduce)').matches;
  let status='',frame=0;
  function maximum(){return viewport?Math.max(0,viewport.scrollHeight-viewport.clientHeight):0}
  function show(text){status=text;if(viewport)viewport.dataset.scrollPhase=phase}
  function reset(){position=0;phase='top';remaining=preferences.topHold*1000;manualWait=0;if(viewport)viewport.scrollTop=0}
  function activate(nextKey,nextViewport){
    if(key===nextKey&&viewport===nextViewport)return;
    const changed=key!==nextKey;viewport=nextViewport;key=nextKey;
    if(changed)reset();else if(viewport){position=Math.min(position,maximum());viewport.scrollTop=position}
    if(!viewport)return;
    // Preserve sub-pixel progress: native scroll positions are rounded on some displays.
    viewport.addEventListener('scroll',()=>{if(manualWait>0||paused||!preferences.enabled)position=viewport.scrollTop},{passive:true});
    const interact=()=>{manualWait=10000;last=0};
    viewport.addEventListener('wheel',interact,{passive:true});viewport.addEventListener('pointerdown',interact,{passive:true});
    viewport.addEventListener('keydown',event=>{if(['ArrowDown','ArrowUp','PageDown','PageUp','Home','End',' '].includes(event.key))interact()});
  }
  function tick(time){
    const elapsed=last?Math.min(time-last,80):0;last=time;
    const max=maximum();
    if(document.hidden){last=0}
    else if(!viewport||max<2)show('当前项目已全部展示');
    else if(paused||!preferences.enabled)show('已暂停 · 可手动浏览');
    else if(manualWait>0){manualWait=Math.max(0,manualWait-elapsed);show('手动浏览 · 稍后继续');if(!manualWait){phase=position<=1?'top':'moving';remaining=preferences.topHold*1000}}
    else if(phase==='top'){
      // Resizing or live updates may change scroll geometry; keep first place visible during its hold.
      position=0;viewport.scrollTop=0;remaining-=elapsed;show('榜首停留 '+Math.max(1,Math.ceil(remaining/1000))+' 秒');
      if(remaining<=0){position=max;viewport.scrollTop=max;phase='bottom';remaining=preferences.bottomHold*1000;if(!matchMedia('(prefers-reduced-motion: reduce)').matches)viewport.animate([{opacity:.35},{opacity:1}],{duration:550,easing:'ease-out'})}
    }else if(phase==='bottom'){
      position=max;viewport.scrollTop=max;remaining-=elapsed;show('底部停留 '+Math.max(1,Math.ceil(remaining/1000))+' 秒');if(remaining<=0)phase='moving';
    }else{
      position=Math.max(0,position-preferences.speed*elapsed/1000);viewport.scrollTop=position;show('缓慢向上滚动');
      if(position<=.5){position=0;viewport.scrollTop=0;phase='top';remaining=preferences.topHold*1000}
    }
    frame=requestAnimationFrame(tick);
  }
  document.addEventListener('visibilitychange',()=>{last=0});
  window.addEventListener('resize',()=>{if(viewport){position=Math.min(position,maximum());viewport.scrollTop=position}});
  frame=requestAnimationFrame(tick);
  return {activate,capture(){if(viewport&&(manualWait>0||paused||!preferences.enabled))position=viewport.scrollTop},configure(options={}){
    const top=options.scroll_top_hold??12,bottom=options.scroll_bottom_hold??3;
    if(top!==preferences.topHold&&phase==='top')remaining=top*1000;
    if(bottom!==preferences.bottomHold&&phase==='bottom')remaining=bottom*1000;
    preferences={speed:options.scroll_speed??16,topHold:top,bottomHold:bottom,enabled:options.scroll_enabled!==false};
  }};
})();
