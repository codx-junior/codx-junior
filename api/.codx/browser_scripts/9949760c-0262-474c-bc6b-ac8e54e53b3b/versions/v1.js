const pause=(a,b)=>new Promise(r=>setTimeout(r,a+Math.random()*(b-a)));
const txt=el=>(el?.innerText||'').replace(/\s+/g,' ').trim();
const btn=re=>[...document.querySelectorAll('button')].find(b=>re.test(txt(b))&&!b.disabled);
const setVal=(el,v)=>{const proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
  const d=Object.getOwnPropertyDescriptor(proto,'value'); d.set.call(el,v);
  el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));};
const pageNum=()=>{const m=(document.body.innerText||'').match(/(\d+)\s*\/\s*(\d+)\s*pages/);return m?m[0]:null;};
function qText(el){let n=el,best='';for(let i=0;i<7&&n;i++){n=n.parentElement;if(!n)break;
  const t=(n.innerText||'').replace(/\s+/g,' ').trim();if(t&&t.length<450)best=t;if(t.length>450)break;}return best;}
const NUM=[[/(current location|ubicaci[oó]n actual|location \(city\))/i,'Palma de Mallorca, Spain'],
  [/(expectativas|salary expectation|expected salary|pretensi)/i,'80000'],
  [/(años|years)[^?]{0,90}(clientes|client-facing|preventa|presales|poc)/i,'15'],
  [/(años|years)[^?]{0,90}(ia|ai|llm|nlp|rag|generativa)/i,'3'],
  [/(años|years)[^?]{0,90}(experiencia|experience|desarrollo|development)/i,'15']];
const YES=[/(ingl[eé]s|english)/i,/(voice ai|agentes de voz|stt|tts)/i,
  /(llms?|nlp|rag|ia generativa|generative ai)/i,
  /hands-on python|python experience|production-grade/i,
  /(permiso de trabajo|work (permit|authorization)|autorizaci[oó]n para trabajar|right to work)/i];
const NO=[/(reubicaci|relocat)/i,/(alem[aá]n|german)\b/i];
const BLOCK=[/bedrock/i,/vertex/i,/azure openai/i,/kafka platform|kafka.{0,20}architect/i];
async function fillPage(){
  const unanswered=[], filled=[];
  for(const el of [...document.querySelectorAll('input,textarea,select')]){
    if(el.type==='hidden'||el.type==='search') continue;
    const q=qText(el)||'';
    if(el.type==='radio'){ const group=[...document.querySelectorAll('input[type=radio]')].filter(r=>qText(r)===q);
      const g=group.length?group:[el];
      if(g.some(r=>r.checked)) continue;
      if(BLOCK.some(re=>re.test(q))){ unanswered.push(q.slice(0,140)); continue; }
      let want='';
      if(YES.some(re=>re.test(q))) want='yes'; else if(NO.some(re=>re.test(q))) want='no';
      if(!want){ unanswered.push(q.slice(0,140)); continue; }
      const t=g.find(r=>{const lt=txt(r.closest('label')||r.parentElement);
        return want==='yes'?/^(yes|s[ií])$/i.test(lt):/^(no)$/i.test(lt);});
      if(t){ await pause(700,1600); t.click(); filled.push(q.slice(0,45)+'='+want); } else unanswered.push(q.slice(0,140));
      continue; }
    if(el.tagName==='SELECT'){ if(!el.value){ const o=[...el.options].find(x=>x.value&&!/select|choose/i.test(x.text));
        if(o){ await pause(600,1400); el.value=o.value; el.dispatchEvent(new Event('change',{bubbles:true})); filled.push('select='+o.text.slice(0,30)); } } continue; }
    if(el.type==='checkbox'){ if(!el.checked&&(el.required||el.getAttribute('aria-required')==='true')){ await pause(600,1400); el.click(); filled.push('checkbox=yes'); } continue; }
    if((el.value||'').trim()) continue;
    if(!(el.required||el.getAttribute('aria-required')==='true')) continue;
    if(BLOCK.some(re=>re.test(q))){ unanswered.push(q.slice(0,140)); continue; }
    const m=NUM.find(([re])=>re.test(q));
    if(!m){ unanswered.push(q.slice(0,140)); continue; }
    await pause(700,1800); el.focus(); setVal(el,m[1]); filled.push(q.slice(0,45)+'='+m[1]); await pause(400,900);
  }
  return {filled,unanswered};
}
const meta=(()=>{const h=document.querySelector('h1');const comp=document.querySelector('.job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name, a[href*="/company/"]');
  return {title:txt(h), company:txt(comp), url:location.href};})();
const log=[];
if(!btn(/easy apply|solicitud sencilla/i)) return {stage:'no-easy-apply', meta};
await pause(2200,4800); btn(/easy apply|solicitud sencilla/i).click(); await pause(3200,7000);
for(let i=1;i<=12;i++){
  const before=pageNum();
  const r=await fillPage(); log.push({p:before,f:r.filled,u:r.unanswered});
  if(r.unanswered.length) return {stage:'stopped-unanswered', page:before, meta, log, questions:[...new Set(r.unanswered)]};
  if(btn(/submit application|^submit$|enviar solicitud|enviar candidatura/i))
    return {stage:'AT-REVIEW-NOT-SUBMITTED', page:before, meta, log};
  const next=btn(/^next$|siguiente|^review$|revisar/i);
  if(!next) return {stage:'no-next', page:before, meta, log};
  await pause(2400,5200); next.click(); await pause(3200,6800);
  if(pageNum()===before && !btn(/submit application|^submit$|enviar solicitud/i)) return {stage:'stalled', page:before, meta, log,
    hint:(document.body.innerText||'').split('\n').map(s=>s.trim()).filter(Boolean).slice(0,10)};
}
return {stage:'max-pages', meta, log};