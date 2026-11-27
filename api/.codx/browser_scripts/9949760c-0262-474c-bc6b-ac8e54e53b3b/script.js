// v2 — confirmed 2026-11-26 on Binaia, Jobgether, HBX, NTT DATA, Aubay, Actual Talent.
// Run as in-page JS with a top-level return, on a https://www.linkedin.com/jobs/view/<id>/ tab.
const pause=(a,b)=>new Promise(r=>setTimeout(r,a+Math.random()*(b-a)));
const txt=el=>(el?.innerText||'').replace(/\s+/g,' ').trim();
const btn=re=>[...document.querySelectorAll('button')].find(b=>re.test(txt(b))&&!b.disabled);
const setVal=(el,v)=>{const proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
  const d=Object.getOwnPropertyDescriptor(proto,'value'); d.set.call(el,v);
  el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));};
const pageNum=()=>{const m=(document.body.innerText||'').match(/(\d+)\s*\/\s*(\d+)\s*pages/);return m?m[0]:null;};
// KEY: the question lives on aria-label / name; the visible option text is the GRANDPARENT's <p>.
const qOf=el=>{const a=el.getAttribute('aria-label'); if(a&&a.trim()) return a.trim();
  const n=el.getAttribute('name'); if(n&&n.length>12&&/[¿?]|años|years|experiencia/i.test(n)) return n;
  let p=el,best='';for(let i=0;i<6&&p;i++){p=p.parentElement;if(!p)break;
    const t=(p.innerText||'').replace(/\s+/g,' ').trim(); if(t&&t.length<400)best=t; if(t.length>400)break;} return best;};
const labOf=r=>txt(r.parentElement&&r.parentElement.parentElement)||txt(r.parentElement);
const PM=/gestionando|gesti[oó]n de proyectos|project manage|\bpmo\b|jefe de proyecto|jefe\/a de proyecto|proyectos tecnol[oó]gicos|delivery metrics/i;
const BLOCK=[/bedrock/i,/vertex/i,/azure openai/i,/kafka platform|kafka.{0,25}architect/i,/openshift/i,/\boracle\b|pl\/sql/i,/git.{0,30}kubernetes|kubernetes.{0,30}git/i];
const TECH=/java|python|react|node|typescript|angular|vue|fastapi|azure|aws|gcp|kubernetes|k8s|docker|kafka|sql|oracle|\.net|c#|csharp|php|\bgo\b|rust|scala|spark|databricks|terraform|jquery|uml|nlp|llm|rag|machine learning|devops|salesforce|sap|odoo|mongo|postgres/i;
const NUM=[
  [/kubernetes|k8s/i,'0'],[/databricks/i,'0'],
  [/(salar|salary|expectativa|pretensi|pretension|banda)/i,'60000'],
  [/(d[ií]as|days)[^?]{0,50}(incorporar|preaviso|notice)/i,'0'],
  [/(años|years)[^?]{0,95}\bjava\b/i,'10'],[/\bjava\b[^?]{0,45}(años|years)/i,'10'],
  [/(años|years)[^?]{0,95}javascript/i,'10'],[/javascript[^?]{0,45}(años|years)/i,'10'],
  [/(años|years)[^?]{0,95}azure/i,'3'],[/azure[^?]{0,45}(años|years)/i,'3'],
  [/(años|years)[^?]{0,95}python/i,'10'],[/python[^?]{0,45}(años|years)/i,'10'],
  [/(años|years)[^?]{0,95}(react|node)/i,'10'],[/\b(react|node)\b[^?]{0,45}(años|years)/i,'10'],
  [/(años|years)[^?]{0,95}typescript/i,'8'],[/typescript[^?]{0,45}(años|years)/i,'8'],
  [/(años|years)[^?]{0,95}(vue|angular|fastapi)/i,'4'],
  [/(años|years)[^?]{0,95}(\bia\b|\bai\b|llm|nlp|rag|generativ)/i,'3'],
  [/(años|years)[^?]{0,95}(experiencia|experience|desarrollo|development|programando|c[oó]digo)/i,'20'],
  [/(años|years)/i,'20']];
const YES=[/(ingl[eé]s|english)/i,/(llms?|nlp|rag|ia generativa|generative ai)/i,
  /hands-on python|python experience|production-grade/i,
  /(permiso de trabajo|work (permit|authorization)|autorizaci[oó]n para trabajar|right to work|residencia|residence|nacionalidad)/i,
  /(disponibilidad|availability|incorporaci[oó]n|start date|comienzo)/i,
  /angular/i,/nivel experto en (java|javascript)/i,
  /(mensajer[ií]a|messaging).{0,40}kafka|kafka.{0,40}(mensajer|messaging|stream)/i,
  /nosql|bases de datos no relacionales/i];
const NO=[/(reubicaci|relocat)/i,/(patrocinio|sponsorship|visa sponsor)/i,/(alem[aá]n|german)\b/i,/kubernetes|openshift/i];
async function fillPage(){
  const unanswered=[], filled=[]; const done=new Set();
  for(const el of [...document.querySelectorAll('input,textarea,select')]){
    if(el.type==='hidden'||el.type==='search') continue;
    const q=qOf(el)||'';
    if(el.type==='radio'){
      const key=el.name||q; if(done.has(key)) continue;
      const group=el.name?[...document.querySelectorAll(`input[type=radio][name="${CSS.escape(el.name)}"]`)]:[el];
      done.add(key);
      if(group.some(r=>r.checked)) continue;
      if(PM.test(q)||BLOCK.some(re=>re.test(q))){ unanswered.push(q.slice(0,150)); continue; }
      let want=''; if(YES.some(re=>re.test(q))) want='yes'; else if(NO.some(re=>re.test(q))) want='no';
      if(!want){ unanswered.push(q.slice(0,150)); continue; }
      const t=group.find(r=>{const lt=labOf(r); return want==='yes'?/^(yes|s[ií]\b)/i.test(lt):/^no\b/i.test(lt);});
      if(!t){ unanswered.push('NOLABEL:'+q.slice(0,110)); continue; }
      await pause(600,1500); t.click(); await pause(400,900);
      if(!t.checked){ const p=t.parentElement?.parentElement; if(p){ p.click(); await pause(400,800);} }
      if(t.checked) filled.push(q.slice(0,48)+'='+want); else unanswered.push('CLICKFAIL:'+q.slice(0,110));
      continue; }
    if(el.tagName==='SELECT'){ if(!el.value){ const opts=[...el.options].filter(x=>x.value&&!/select|choose|selecciona/i.test(x.text));
        if(!opts.length) continue;
        let pick=opts.find(x=>/\d/.test(x.text)&&(()=>{const ns=(x.text.match(/\d+/g)||[]).map(Number);
          return ns.some(n=>n>=60000&&n<=70000)||(Math.min(...ns)<=60000&&Math.max(...ns)>=60000);})());
        if(!pick) pick=opts[0];
        await pause(600,1400); el.value=pick.value; el.dispatchEvent(new Event('change',{bubbles:true}));
        filled.push('select='+pick.text.slice(0,30)); } continue; }
    if(el.type==='checkbox'){ if(!el.checked&&(el.required||el.getAttribute('aria-required')==='true')){ await pause(600,1400); el.click(); filled.push('checkbox=yes'); } continue; }
    if((el.value||'').trim()) continue;
    if(!(el.required||el.getAttribute('aria-required')==='true')) continue;
    if(PM.test(q)||BLOCK.some(re=>re.test(q))){ unanswered.push(q.slice(0,150)); continue; }
    const m=NUM.find(([re])=>re.test(q));
    if(!m){ unanswered.push(q.slice(0,150)); continue; }
    if(/(años|years)/i.test(q) && TECH.test(q) && !NUM.slice(0,14).some(([re])=>re.test(q))){ unanswered.push('TECH-YEARS:'+q.slice(0,120)); continue; }
    await pause(700,1800); el.focus(); setVal(el,m[1]); filled.push(q.slice(0,48)+'='+m[1]); await pause(400,900);
  }
  return {filled,unanswered};
}
const meta=(()=>{const h=document.querySelector('h1');
  const comp=document.querySelector('.job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name');
  return {title:(h?.innerText||'').trim().slice(0,60), company:txt(comp), url:location.href.split('?')[0]};})();
const log=[];
try{
if(!btn(/easy apply|solicitud sencilla/i)) return JSON.stringify({stage:'no-easy-apply', meta});
await pause(2000,4200); btn(/easy apply|solicitud sencilla/i).click(); await pause(3200,6500);
for(let i=1;i<=12;i++){
  const before=pageNum();
  const r=await fillPage(); log.push({p:before,f:r.filled,u:r.unanswered});
  if(r.unanswered.length) return JSON.stringify({stage:'stopped-unanswered', page:before, meta, log, questions:[...new Set(r.unanswered)]});
  if(btn(/submit application|^submit$|enviar solicitud|enviar candidatura/i))
    return JSON.stringify({stage:'AT-REVIEW-NOT-SUBMITTED', page:before, meta, log});
  const next=btn(/^next$|siguiente|^review$|revisar/i);
  if(!next) return JSON.stringify({stage:'no-next', page:before, meta, log});
  await pause(2200,4800); next.click(); await pause(3000,6200);
  if(pageNum()===before && !btn(/submit application|^submit$|enviar solicitud/i))
    return JSON.stringify({stage:'stalled', page:before, meta, log});
}
return JSON.stringify({stage:'max-pages', meta, log});
}catch(e){ return JSON.stringify({stage:'error', err:String(e).slice(0,180), meta, log}); }