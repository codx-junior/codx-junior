const pause=(a,b)=>new Promise(r=>setTimeout(r,a+Math.random()*(b-a)));
const txt=el=>(el?.innerText||'').replace(/\s+/g,' ').trim();
const btn=re=>[...document.querySelectorAll('button')].find(b=>re.test(txt(b))&&!b.disabled);
const setVal=(el,v)=>{
  const proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
  const d=Object.getOwnPropertyDescriptor(proto,'value');
  d.set.call(el,v);
  el.dispatchEvent(new Event('input',{bubbles:true}));
  el.dispatchEvent(new Event('change',{bubbles:true}));
};
const NUM=[
  [/(años|years)[^?]{0,90}(clientes|client-facing|preventa|presales|poc)/i,'15'],
  [/(expectativas|salary expectation|pretensi|salary)/i,'80000'],
  [/(años|years)[^?]{0,90}(ia|ai|llm|nlp|rag|generativa|generative)/i,'3'],
  [/(años|years)[^?]{0,90}(experiencia|experience|desarrollo|development)/i,'15']
];
const YES=[
  /(ingl[eé]s|english)/i, /(voice ai|agentes de voz|stt|tts)/i,
  /(llms?|nlp|rag|ia generativa|generative ai)/i,
  /(permiso de trabajo|work (permit|authorization)|autorizaci[oó]n para trabajar|right to work)/i,
  /(experiencia|experience)[^?]{0,60}(ia|ai)\b/i
];
const NO=[/(reubicaci|relocat)/i,/(alem[aá]n|german)\b/i];
async function fillPage(){
  const groups=[...document.querySelectorAll('.jobs-easy-apply-form-section__grouping, .fb-dash-form-element, [data-test-text-entity-list-form-component]')];
  const unanswered=[], filled=[];
  for(const g of groups){
    const el=g.querySelector('input:not([type=hidden]),textarea,select');
    if(!el) continue;
    const label=txt(g.querySelector('label,legend,.fb-dash-form-element__label')||g);
    const req=g.innerText.includes('*');
    if(el.type==='radio'){
      const radios=[...g.querySelectorAll('input[type=radio]')];
      if(radios.some(r=>r.checked)) continue;
      let want='';
      if(YES.some(re=>re.test(label))) want='yes';
      else if(NO.some(re=>re.test(label))) want='no';
      if(!want){ if(req) unanswered.push(label); continue; }
      const t=radios.find(r=>{const lt=txt(r.closest('label')||r.parentElement);
        return want==='yes'?/^(yes|s[ií])$/i.test(lt):/^(no)$/i.test(lt);});
      if(t){ await pause(700,1600); t.click(); filled.push(label+'='+want); }
      else if(req) unanswered.push(label);
      continue;
    }
    if(el.tagName==='SELECT'){
      if(!el.value){ const o=[...el.options].find(x=>x.value&&!/select/i.test(x.text));
        if(o){ await pause(600,1400); el.value=o.value; el.dispatchEvent(new Event('change',{bubbles:true})); filled.push(label+'='+o.text); } }
      continue;
    }
    if(el.type==='checkbox'){ if(!el.checked&&req){ await pause(600,1400); el.click(); filled.push(label+'=checked'); } continue; }
    if((el.value||'').trim()) continue;
    if(el.tagName==='TEXTAREA'||el.type==='text'||el.type==='number'||!el.type){
      const m=NUM.find(([re])=>re.test(label));
      if(!m){ if(req) unanswered.push(label); continue; }
      await pause(700,1800); el.focus(); setVal(el,m[1]); filled.push(label+'='+m[1]);
      await pause(400,900);
    }
  }
  return {filled,unanswered};
}
const log=[];
const ap=btn(/easy apply|solicitud sencilla/i);
if(ap){ await pause(2200,4800); ap.click(); await pause(3200,7000); }
for(let i=1;i<=8;i++){
  let guard=0, sub=null, next=null;
  while(guard++<25){
    sub=btn(/submit application|^submit$|enviar solicitud|enviar candidatura/i);
    next=btn(/^next$|siguiente|^review$|revisar/i);
    if(sub||next) break;
    await pause(700,1300);
  }
  if(!sub&&!next) return {stage:'no-form', url:location.href, log, body:(document.body.innerText||'').slice(0,300)};
  const r=await fillPage();
  log.push({page:i,...r});
  if(r.unanswered.length) return {stage:'stopped-unanswered', log, questions:r.unanswered, url:location.href};
  if(sub){ await pause(3200,6800); sub.click(); await pause(4200,8200);
    return {stage:'submitted', log, after:(document.body.innerText||'').slice(0,200)}; }
  await pause(2400,5200); next.click(); await pause(3000,6500);
}
return {stage:'max-pages', log, url:location.href};