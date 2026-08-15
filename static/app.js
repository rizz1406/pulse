let pending=null, charts={};
let stepsState={saved:0,target:5000,editing:false,saving:false};
const fmtMacro=v=>{const n=Number(v);return Number.isFinite(n)?Number(n.toFixed(1)):0;};
const fmtOptional=v=>v==null?'—':`${fmtMacro(v)}g`;

/* ---------- AUTH ---------- */
async function checkAuth(){
  const r=await fetch('/api/me'); const d=await r.json();
  if(d.authed){ showApp(); } else { document.getElementById('login').style.display='flex'; }
}
async function doLogin(){
  const pass=document.getElementById('passInput').value;
  const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({passcode:pass})});
  const d=await r.json();
  if(d.ok){ showApp(); } else { document.getElementById('loginErr').textContent=d.error||'Wrong passcode'; }
}
document.getElementById('passInput').addEventListener('keydown',e=>{if(e.key==='Enter')doLogin();});
let _startupExtrasLoaded=false;
async function showApp(){
  document.getElementById('login').style.display='none';
  document.getElementById('appWrap').style.display='block';
  document.getElementById('dock').style.display='block';
  try{
    const cached=JSON.parse(localStorage.getItem('pulse-cache:/api/today'));
    if(cached&&cached.totals) renderToday(cached);
    const weekly=JSON.parse(localStorage.getItem('pulse-cache:/api/analytics/weekly'));
    if(weekly) renderWeeklyStreak(weekly);
  }catch(e){}
  await refreshToday();
  if(!_startupExtrasLoaded){
    _startupExtrasLoaded=true;
    const loadExtras=()=>{
      loadWeeklyStreak();
      loadRecap();
      setTimeout(()=>loadSuggestions(),300);
    };
    if('requestIdleCallback' in window) requestIdleCallback(loadExtras,{timeout:1200});
    else setTimeout(loadExtras,200);
  }
}
function showLogin(){
  document.getElementById('appWrap').style.display='none';
  document.getElementById('dock').style.display='none';
  document.getElementById('login').style.display='flex';
}

/* ---------- TABS ---------- */
let _chartLoading=false;
function ensureChart(cb){
  if(window.Chart){ cb(); return; }
  if(_chartLoading) return;
  _chartLoading=true;
  const s=document.createElement('script');
  s.src='https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js';
  s.onload=()=>{_chartLoading=false; cb();};
  s.onerror=()=>{_chartLoading=false; toast('Charts failed to load');};
  document.head.appendChild(s);
}
function switchTab(t){
  document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x.dataset.tab===t));
  document.querySelectorAll('.section').forEach(x=>x.classList.remove('active'));
  document.getElementById('tab-'+t).classList.add('active');
  if(t==='analytics') ensureChart(()=>loadAnalytics(currentDays));
  if(t==='progress') ensureChart(()=>loadProgress());
}

/* ---------- CACHED FETCH (works offline with last-good data) ---------- */
async function getJSON(url){
  try{
    const r=await fetch(url);
    if(r.status===401){location.reload();return null;}
    const d=await r.json();
    try{ localStorage.setItem('pulse-cache:'+url, JSON.stringify(d)); }catch(e){}
    return d;
  }catch(e){
    const c=localStorage.getItem('pulse-cache:'+url);
    if(c){ toast('Offline — showing saved data'); return JSON.parse(c); }
    toast('Network error'); return null;
  }
}

/* ---------- PROGRESS ---------- */
async function loadProgress(){
  const d=await getJSON('/api/progress?days=60');
  if(!d) return;
  renderLeanBulkReport(d.weekly_report);
  const hero=document.getElementById('progHero');
  if(!d.current){
    hero.innerHTML='<div class="empty" style="padding:20px">Log your weight to start tracking progress.<br>Just type "I weigh 77kg" below.</div>';
    document.getElementById('paceBox').innerHTML='';
    document.getElementById('photoStrip').innerHTML='';
    if(charts['weightTrend'])charts['weightTrend'].destroy();
    return;
  }
  const lost=d.start!=null?(d.start-d.current).toFixed(1):0;
  const bmiTag = d.bmi!=null ? `<span class="bmi-tag">BMI ${d.bmi}</span>` : '';
  hero.innerHTML=`<div class="cur">${d.current}<small>kg</small>${bmiTag}</div>
    <div class="sub">avg intake ${d.avg_cal} kcal/day${d.target_cal?` · target ${d.target_cal}`:''}</div>
    ${d.start!=null?`<div class="lost">${lost>0?'Down':'Up'} <b>${Math.abs(lost)}kg</b> from ${d.start}kg</div>`:''}`;
  // weight chart
  drawLine('weightTrend',d.weights.map(w=>w.day),[{label:'kg',data:d.weights.map(w=>w.kg),color:'#c4ff4d',fill:true,spanGaps:true}]);
  // pace / adaptive lean-bulk coach
  const pace=document.getElementById('paceBox');
  if(d.coach&&d.coach.active){
    const c=d.coach;
    const reminderOff=localStorage.getItem('pulse-weight-reminder-disabled')==='1';
    if(!c.enough_data){
      pace.innerHTML=`<div class="coach-title">Lean-bulk coach is collecting your baseline</div>
        <div class="coach-stats"><b>${c.weigh_ins}/7</b> weigh-ins <span>·</span> <b>${c.span_days}/14</b> days</div>
        <div class="pace-note">Daily morning weigh-ins are averaged, so water and meal fluctuations do not change your calories.</div>
        <button class="coach-reminder" onclick="toggleWeightReminder()">${reminderOff?'Enable':'Disable'} daily reminder</button>`;
    }else{
      const rate=Number(c.rate_kg_per_week);
      const onTarget=rate>=c.target_min&&rate<=c.target_max;
      const adj=Number(c.calorie_adjustment||0);
      pace.innerHTML=`<div class="pace-line ${onTarget?'pace-good':'pace-warn'}">${onTarget?'On target':'Adjusting carefully'} · ${rate>=0?'+':''}${rate.toFixed(2)} kg/week</div>
        <div class="coach-grid">
          <div><b>${Number(c.average_7d).toFixed(2)} kg</b><span>7-day average</span></div>
          <div><b>${c.target_min}–${c.target_max}</b><span>kg/week goal</span></div>
          <div><b>${adj>=0?'+':''}${adj} kcal</b><span>adaptive change</span></div>
        </div>
        <div class="pace-note">Next review ${c.next_review}. Changes happen at most weekly and are capped at 100–150 kcal per review.</div>
        <button class="coach-reminder" onclick="toggleWeightReminder()">${reminderOff?'Enable':'Disable'} daily reminder</button>`;
    }
  } else if(d.rate_kg_per_week==null){
    pace.innerHTML='<div class="pace-note">Log your weight a few more times (a week apart) to see your pace.</div>';
  } else {
    const rate=d.rate_kg_per_week;
    const cutting=d.objective&&d.objective.startsWith('cut');
    let cls='pace-good', msg='';
    if(cutting){
      if(rate<=-0.25&&rate>=-0.7){cls='pace-good';msg=`Losing ${Math.abs(rate)}kg/week — right in the ideal zone. 🎯`;}
      else if(rate>-0.25){cls='pace-warn';msg=`Only ${Math.abs(rate)}kg/week — slower than target. Trim ~150 kcal or tighten portions.`;}
      else {cls='pace-warn';msg=`Losing ${Math.abs(rate)}kg/week — a bit fast, you may lose muscle. Add ~150 kcal.`;}
    } else {
      msg=`Changing ${rate}kg/week.`;
    }
    pace.innerHTML=`<div class="pace-line ${cls}">${msg}</div>
      <div class="pace-note">Based on your weight trend over the period. Weigh in 1–2× a week, same time of day, for the cleanest signal.</div>`;
  }
  // progress photos
  const strip=document.getElementById('photoStrip');
  if(d.photos && d.photos.length){
    strip.innerHTML=`<h3 style="font-family:'Space Grotesk';font-size:15px;margin:4px 0 10px">Progress photos <span class="hint">attach on the next weigh-in</span></h3>
      <div class="photo-scroll">${d.photos.map((p,i)=>`<img src="${p.photo}" alt="${p.weight_kg}kg ${p.day}" onclick="openPhotoStrip(${i})">`).join('')}</div>`;
    window._photos=d.photos;
    strip.style.display='block';
  } else {
    strip.style.display='none';
  }
}
let _photoView=null;
function openPhotoStrip(i){
  const p=(window._photos||[])[i]; if(!p) return;
  const old=document.getElementById('photoView'); if(old) old.remove();
  const v=document.createElement('div');
  v.id='photoView';
  v.style.cssText='position:fixed;inset:0;z-index:200;background:rgba(0,0,0,.92);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:20px';
  v.innerHTML=`<img src="${p.photo}" style="max-width:100%;max-height:78vh;border-radius:16px;object-fit:contain">
    <div style="color:#eee;font-size:14px">${p.weight_kg} kg · ${p.day}</div>
    <button class="btn-cancel" onclick="document.getElementById('photoView').remove()" style="min-height:44px;padding:10px 26px">Close</button>`;
  v.onclick=e=>{ if(e.target===v) v.remove(); };
  document.body.appendChild(v);
  _photoView=v;
}

/* ---------- INPUT ---------- */
const ta=document.getElementById('msg');
ta.addEventListener('input',()=>{ta.style.height='auto';ta.style.height=Math.min(ta.scrollHeight,100)+'px';checkAutocomplete();});
ta.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendText();}if(e.key==='Escape')hideAutocomplete();});
let _acTimer=null;
async function checkAutocomplete(){
  clearTimeout(_acTimer);
  const q=ta.value.trim();
  if(q.length<1){hideAutocomplete();return;}
  _acTimer=setTimeout(async()=>{
    try{
      const r=await fetch('/api/autocomplete?q='+encodeURIComponent(q));
      const d=await r.json();
      if(!d.suggestions||!d.suggestions.length){hideAutocomplete();return;}
      const el=document.getElementById('autocomplete');
      el.innerHTML=d.suggestions.map(s=>
        `<div class="ac-item" onclick="selectAC('${jsStr(s.name)}')">
          <span class="ac-name">${esc(s.name)}</span>
          ${s.calories?`<span class="ac-cal">${s.calories} kcal</span>`:'<span class="ac-src">database</span>'}
        </div>`
      ).join('');
      el.classList.add('show');
    }catch(e){}
  },200);
}
function selectAC(name){
  ta.value=name; hideAutocomplete(); sendText();
}
function hideAutocomplete(){
  const el=document.getElementById('autocomplete');
  if(el){el.innerHTML='';el.classList.remove('show');}
}
document.addEventListener('click',e=>{
  if(!e.target.closest('.input-box')&&!e.target.closest('#autocomplete'))hideAutocomplete();
});

async function sendText(){
  const text=ta.value.trim(); if(!text) return;
  ta.value=''; ta.style.height='auto';
  await sendPayload({json:{text}});
}
async function sendPayload(opts){
  const btn=document.getElementById('sendBtn'); btn.innerHTML='<div class="spinner"></div>';
  try{
    let res;
    if(opts.json){
      res=await fetch('/api/log',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(opts.json)});
    } else {
      res=await fetch('/api/log',{method:'POST',body:opts.form});
    }
    const d=await res.json();
    if(res.status===401){ showLogin(); return; }
    if(!res.ok){ toast(d.error||'Something went wrong'); return; }
    handleResult(d);
  }catch(e){ toast('Network error'); }
  finally{ btn.innerHTML='➤'; }
}

/* ---------- PHOTO (camera / gallery) ---------- */
async function onPhoto(input){
  const file=input.files[0]; if(!file) return;
  input.value=''; // reset so same photo can be picked again
  const form=new FormData(); form.append('media',file,file.name||'photo.jpg');
  document.getElementById('sendBtn').innerHTML='<div class="spinner"></div>';
  await sendPayload({form});
}

/* Progress photo — attach to the pending weight entry as a data URI. */
function onProgPhoto(input){
  const file=input.files[0]; if(!file) return;
  input.value='';
  if(!file.type.startsWith('image/')){ toast('Pick an image'); return; }
  const reader=new FileReader();
  reader.onload=e=>{
    const uri=e.target.result;
    if(uri.length>3*1024*1024){ toast('Photo too large — max 3MB'); return; }
    if(pending && pending.type==='weight'){
      pending._photo=uri;
      const s=document.getElementById('sheet');
      s.innerHTML=s.innerHTML.replace(
        `<button class="btn-cancel" onclick="document.getElementById('progPhotoInput').click()">📷 Photo</button>`,
        `<div class="prog-photo-preview"><img src="${uri}" alt="progress"><div class="del" onclick="clearWeightPhoto()">✕</div></div>
         <button class="btn-cancel" onclick="document.getElementById('progPhotoInput').click()">📷 Photo</button>`);
    }
  };
  reader.readAsDataURL(file);
}
function clearWeightPhoto(){
  if(pending && pending.type==='weight'){
    delete pending._photo;
    const s=document.getElementById('sheet');
    const p=s.querySelector('.prog-photo-preview'); if(p) p.remove();
  }
}

/* ---------- RESULT / PREVIEW ---------- */
function handleResult(d){
  if(d.type==='chat'){ showChat(d.reply); return; }
  // New pills-based clarification (from ambiguity check)
  if(d.type==='food' && d.pills && d.pills.length){
    showPillClarify(d);
    return;
  }
  // Legacy clarification (string options)
  if(d.type==='food' && d.needs_clarification && d.clarify_options && d.clarify_options.length){
    showClarify(d);
    return;
  }
  pending=d;
  const s=document.getElementById('sheet');
  if(d.type==='food'){
    const noNutrition = d.calories===0 && d.source==='gemini_fallback';
    const warnings=(d.accuracy_warnings||[]).map(w=>`<div class="accuracy-warning">⚠️ ${esc(w)}</div>`).join('');
    s.innerHTML=`<div class="ph">🍽 ${esc(d.item_name)}</div>
      <div class="kcal">${d.calories} kcal</div>
      <div class="macros">
        <span>💪 ${fmtMacro(d.protein_g)}g protein</span><span>🍞 ${fmtMacro(d.carbs_g)}g carbs</span><span>🥑 ${fmtMacro(d.fat_g)}g fat</span>
        <span>🌾 ${fmtOptional(d.fiber_g)} fiber</span><span>🍬 ${fmtOptional(d.sugar_g)} sugar</span></div>
      ${d.accuracy_label?`<div class="accuracy-status ${esc(d.accuracy_level||'estimate')}">
        <b>${esc(d.accuracy_label)}</b><span>${esc(d.accuracy_message||'')}</span></div>`:''}
      <div class="food-edit-grid">
        <label class="wide">Food<input id="pv_name" value="${esc(d.item_name)}"></label>
        <label>kcal<input id="pv_cal" type="number" min="0" value="${d.calories}"></label>
        <label>Protein<input id="pv_p" type="number" min="0" step="0.1" value="${d.protein_g}"></label>
        <label>Carbs<input id="pv_c" type="number" min="0" step="0.1" value="${d.carbs_g}"></label>
        <label>Fat<input id="pv_f" type="number" min="0" step="0.1" value="${d.fat_g}"></label>
        <label>Fiber<input id="pv_fb" type="number" min="0" step="0.1" placeholder="unknown" value="${d.fiber_g==null?'':d.fiber_g}"></label>
        <label>Sugar<input id="pv_sg" type="number" min="0" step="0.1" placeholder="unknown" value="${d.sugar_g==null?'':d.sugar_g}"></label>
      </div>
      ${warnings}
      ${noNutrition?`<div class="note">⚠️ Couldn't find nutrition for this — it will be logged as 0 kcal. Edit it after logging to add calories.</div>`:''}
      ${d.source==='ai_estimate'?`<div class="note">🤖 Estimated by the AI for a standard portion — Log it, then tap the card to adjust if it looks off.</div>`:''}
      ${d.confidence_notes?`<div class="note">${esc(d.confidence_notes)}</div>`:''}
      ${d.source?`<div class="note" style="opacity:.55;font-size:11px">source: ${esc(d.source)}${(d.matched_food&&d.source!=='ai_estimate')?` · matched "${esc(d.matched_food)}"`:''}${d.serving_g?` · serving ${d.serving_g}g`:''}${(d.qty&&d.qty!=1)?` · × ${d.qty}`:''}</div>`:''}
      <div class="sheet-actions">
        <button class="btn-cancel" onclick="closeSheet()">Cancel</button>
        <button class="btn-cancel" onclick="saveCustomFood()">Save custom</button>
        <button class="btn-save" onclick="confirmEntry()">Log it</button></div>`;
  } else if(d.type==='workout'){
    const w=d.weight_kg?`${d.weight_kg}kg`:'bodyweight';
    s.innerHTML=`<div class="ph">🏋 ${esc(d.exercise_name)}</div>
      <div class="macros" style="margin-top:12px"><span>⚖ ${w}</span><span>${d.sets} sets × ${d.reps} reps</span></div>
      ${d.notes?`<div class="note">${esc(d.notes)}</div>`:''}
      <div class="sheet-actions">
        <button class="btn-cancel" onclick="closeSheet()">Cancel</button>
        <button class="btn-save" onclick="confirmEntry()">Log it</button></div>`;
  } else if(d.type==='weight'){
    const pn = d._photo || '';
    s.innerHTML=`<div class="ph">⚖ Body weight</div>
      <div class="kcal">${d.weight_kg} kg</div>
      ${d.notes?`<div class="note">${esc(d.notes)}</div>`:''}
      ${pn?`<div class="prog-photo-preview"><img src="${pn}" alt="progress"><div class="del" onclick="clearWeightPhoto()">✕</div></div>`:''}
      <div class="sheet-actions">
        <button class="btn-cancel" onclick="document.getElementById('progPhotoInput').click()">📷 Photo</button>
        <button class="btn-cancel" onclick="closeSheet()">Cancel</button>
        <button class="btn-save" onclick="confirmEntry()">Log it</button></div>`;
  } else if(d.type==='water'){
    s.innerHTML=`<div class="ph">💧 Water</div>
      <div class="kcal" style="color:#4dd8ff;text-shadow:0 0 24px rgba(77,216,255,.45)">${d.ml} ml</div>
      <div class="sheet-actions">
        <button class="btn-cancel" onclick="closeSheet()">Cancel</button>
        <button class="btn-save" style="background:#4dd8ff" onclick="confirmEntry()">Log it</button></div>`;
  }
  document.getElementById('overlay').classList.add('show');
}
let clarifyCtx=null;
function showClarify(d){
  clarifyCtx={
    original: d._context || d._raw || d.item_name,
    question: d.clarify_question,
    round: d._round || 1,
    fallback: d
  };
  const opts=d.clarify_options.map(o=>
    `<button class="clarify-opt" onclick="answerClarify('${jsStr(o)}')">${esc(o)}</button>`
  ).join('');
  document.getElementById('sheet').innerHTML=
    `<div class="ph">🍽 ${esc(d.item_name)}</div>
     <div class="clarify-q">${esc(d.clarify_question)}</div>
     <div class="clarify-opts">${opts}</div>
     <div class="clarify-skip" onclick="skipClarify()">Skip — just use the estimate (${d.calories} kcal)</div>`;
  document.getElementById('overlay').classList.add('show');
}
async function answerClarify(answer){
  document.getElementById('sheet').innerHTML=
    '<div style="text-align:center;padding:30px"><div class="spinner" style="border-top-color:var(--accent);border-color:rgba(182,255,61,.25);margin:0 auto"></div><div style="color:var(--muted);margin-top:14px;font-size:14px">Recalculating…</div></div>';
  try{
    const r=await fetch('/api/clarify',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({original:clarifyCtx.original,question:clarifyCtx.question,answer,round:clarifyCtx.round})});
    const d=await r.json();
    if(!r.ok){ toast(d.error||'Something went wrong'); closeSheet(); return; }
    handleResult(d); // may show a 2nd question, or the final confirm card
  }catch(e){ toast('Network error'); closeSheet(); }
}
function skipClarify(){
  const d=clarifyCtx.fallback; d.needs_clarification=false;
  clarifyCtx=null; handleResult(d);
}

/* ---------- PILL-BASED CLARIFICATION ---------- */
let pillCtx=null;
function showPillClarify(d){
  pillCtx={
    food_name: d.item_name,
    pills: d.pills,
    default_fallback: d.default_fallback,
    fallback: d
  };
  const pills=d.pills.map(p=>
    `<button class="clarify-pill" onclick="answerPill('${jsStr(p.text)}','${jsStr(p.label)}')">${esc(p.label)}</button>`
  ).join('');
  const defaultBtn = d.default_fallback
    ? `<button class="clarify-default" onclick="useDefaultEstimate()">Use default estimate (${esc(d.default_fallback)})</button>`
    : '';
  document.getElementById('sheet').innerHTML=
    `<div class="ph">🍽 ${esc(d.item_name)}</div>
     <div class="clarify-q">How was your ${esc(d.item_name)} prepared?</div>
     <div class="clarify-pills">${pills}</div>
     ${defaultBtn}
     <div class="sheet-actions">
       <button class="btn-cancel" onclick="closePillClarify()">Cancel</button>
     </div>`;
  document.getElementById('overlay').classList.add('show');
}
async function answerPill(pillText, pillLabel){
  document.getElementById('sheet').innerHTML=
    '<div style="text-align:center;padding:30px"><div class="spinner" style="border-top-color:var(--accent);border-color:rgba(182,255,61,.25);margin:0 auto"></div><div style="color:var(--muted);margin-top:14px;font-size:14px">Looking up nutrition…</div></div>';
  try{
    const r=await fetch('/api/pill',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({pill_text:pillText, food_name:pillCtx.food_name})});
    const d=await r.json();
    if(!r.ok||!d.calories){ toast(d.error||'Could not find nutrition'); closeSheet(); return; }
    d.needs_clarification=false;
    d._raw=pillText;
    pillCtx=null;
    handleResult(d);
  }catch(e){ toast('Network error'); closeSheet(); }
}
async function useDefaultEstimate(){
  if(!pillCtx||!pillCtx.default_fallback) return closePillClarify();
  document.getElementById('sheet').innerHTML=
    '<div style="text-align:center;padding:30px"><div class="spinner" style="border-top-color:var(--accent);border-color:rgba(182,255,61,.25);margin:0 auto"></div><div style="color:var(--muted);margin-top:14px;font-size:14px">Using default estimate…</div></div>';
  try{
    const r=await fetch('/api/pill',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({pill_text:pillCtx.default_fallback, food_name:pillCtx.food_name})});
    const d=await r.json();
    if(!r.ok||!d.calories){ toast(d.error||'Could not find nutrition'); closeSheet(); return; }
    d.needs_clarification=false;
    d._raw=pillCtx.default_fallback;
    pillCtx=null;
    handleResult(d);
  }catch(e){ toast('Network error'); closeSheet(); }
}
function closePillClarify(){ pillCtx=null; closeSheet(); }

function showChat(reply){
  pending=null;
  document.getElementById('sheet').innerHTML=`<div class="chat-bubble">${esc(reply)}</div>
    <div class="sheet-actions"><button class="btn-save" onclick="closeSheet()">Got it</button></div>`;
  document.getElementById('overlay').classList.add('show');
}
function closeSheet(){ document.getElementById('overlay').classList.remove('show'); pending=null; }
async function confirmEntry(){
  if(!pending) return closeSheet();
  if(pending.type==='food') syncPendingFood();
  const body=JSON.parse(JSON.stringify(pending));
  if(body.type==='weight' && body._photo){
    body.photo=body._photo;
    delete body._photo;
  }
  const r=await fetch('/api/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json();
  if(!r.ok||!d.ok){ toast(d.error||'Could not log entry'); return; }
  if(body.type==='food'&&body.source==='nutrition_label'&&body.serving_g>0){
    fetch('/api/custom_food',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({...body,name:body.item_name})}).catch(()=>{});
  }
  const wasWeight = pending && pending.type==='weight';
  const wasWater = pending && pending.type==='water';
  closeSheet();
  if(d.ok){
    toast(wasWeight ? 'Weight logged — targets updated 🎯' : (wasWater ? 'Logged 💧' : 'Logged ✅'));
    refreshToday();
    successBurst();
  }
}
function renderLeanBulkReport(r){
  const card=document.getElementById('leanBulkReportCard');
  const box=document.getElementById('leanBulkReport');
  if(!r||!r.active){card.style.display='none';return;}
  const adherenceClass=v=>v>=90&&v<=110?'good':(v?'warn':'');
  const rate=r.weight_rate==null?null:Number(r.weight_rate);
  const weightGood=rate!=null&&rate>=r.weight_target_min&&rate<=r.weight_target_max;
  const weightText=rate==null?'Collecting':`${rate>=0?'+':''}${rate.toFixed(2)} kg/wk`;
  box.innerHTML=`<div class="bulk-report-grid">
    <div class="bulk-report-item ${weightGood?'good':(rate==null?'':'warn')}"><b>${weightText}</b><span>Weight trend</span><small>target +${r.weight_target_min}–${r.weight_target_max} kg/wk</small></div>
    <div class="bulk-report-item ${adherenceClass(r.calorie_adherence)}"><b>${Number(r.avg_calories).toLocaleString()} kcal</b><span>Average calories</span><small>${r.calorie_adherence}% of ${Number(r.calorie_target).toLocaleString()}</small></div>
    <div class="bulk-report-item ${r.protein_adherence>=90?'good':(r.protein_adherence?'warn':'')}"><b>${r.avg_protein}g</b><span>Average protein</span><small>${r.protein_adherence}% of ${r.protein_target}g</small></div>
    <div class="bulk-report-item"><b>${r.workouts}</b><span>Workouts</span><small>${r.workout_days} training days</small></div>
    <div class="bulk-report-item ${r.avg_steps>=r.step_target?'good':(r.step_days_logged?'warn':'')}"><b>${Number(r.avg_steps).toLocaleString()}</b><span>Average steps</span><small>${r.step_days_hit}/${r.step_days_logged} logged days hit ${Number(r.step_target).toLocaleString()}</small></div>
    <div class="bulk-report-item"><b>${r.days_logged}/7</b><span>Nutrition days</span><small>averages use logged days</small></div>
  </div>`;
  card.style.display='block';
}

function syncPendingFood(){
  if(!pending||pending.type!=='food') return;
  const number=id=>{const el=document.getElementById(id);return el&&el.value!==''?Number(el.value):null;};
  pending.item_name=document.getElementById('pv_name').value.trim()||pending.item_name;
  pending.calories=number('pv_cal')||0;
  pending.protein_g=number('pv_p')||0;
  pending.carbs_g=number('pv_c')||0;
  pending.fat_g=number('pv_f')||0;
  pending.fiber_g=number('pv_fb');
  pending.sugar_g=number('pv_sg');
}

async function saveCustomFood(){
  if(!pending||pending.type!=='food') return;
  syncPendingFood();
  const name=prompt('Custom food name',pending.item_name); if(!name) return;
  const serving=Number(prompt('Serving weight in grams',String(pending.serving_g||30)));
  if(!(serving>0)) return toast('Enter a valid serving weight');
  const body={...pending,name,serving_g:serving};
  const r=await fetch('/api/custom_food',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json(); toast(d.ok?'Custom food saved ✅':(d.error||'Could not save'));
}

/* success pulse + spark flash around the ring on a log */
function successBurst(){
  const wrap=document.getElementById('ringWrap'); if(!wrap) return;
  wrap.classList.remove('burst'); void wrap.offsetWidth; wrap.classList.add('burst');
  const rect=wrap.getBoundingClientRect();
  const cx=rect.left+rect.width/2, cy=rect.top+rect.height/2;
  for(let i=0;i<10;i++){
    const s=document.createElement('div'); s.className='spark';
    s.style.background = i%3===0 ? '#4dd8ff' : (i%3===1 ? '#ffb04d' : '#c4ff4d');
    s.style.left=cx+'px'; s.style.top=cy+'px';
    document.body.appendChild(s);
    const ang=(Math.PI*2*i)/10, dist=60+Math.random()*30;
    const dx=Math.cos(ang)*dist, dy=Math.sin(ang)*dist;
    s.animate([
      {transform:'translate(0,0) scale(1)',opacity:1},
      {transform:`translate(${dx}px,${dy}px) scale(0)`,opacity:0}
    ],{duration:600+Math.random()*200,easing:'cubic-bezier(.2,.8,.2,1)'}).onfinish=()=>s.remove();
  }
}

/* ---------- TODAY ---------- */
let currentDays=30;
async function refreshToday(){
  const d=await getJSON('/api/today');
  if(d){ renderToday(d); maybePromptDailyWeight(d); }
  loadRecents();
}

function localDay(){
  const n=new Date(), pad=v=>String(v).padStart(2,'0');
  return `${n.getFullYear()}-${pad(n.getMonth()+1)}-${pad(n.getDate())}`;
}
function maybePromptDailyWeight(d){
  if(!d.weigh_in_due||!d.coach||!d.coach.active||!navigator.onLine) return;
  if(localStorage.getItem('pulse-weight-reminder-disabled')==='1') return;
  const day=localDay();
  if(localStorage.getItem('pulse-weight-reminder-seen')===day) return;
  if(document.getElementById('overlay').classList.contains('show')) return;
  localStorage.setItem('pulse-weight-reminder-seen',day);
  document.getElementById('sheet').innerHTML=`
    <div class="ph">⚖️ Morning weigh-in</div>
    <div class="pace-note">For the cleanest trend, weigh after the bathroom and before food or water.</div>
    <label class="weight-prompt-label">Weight (kg)
      <input id="dailyWeightInput" type="number" min="30" max="300" step="0.1" inputmode="decimal" placeholder="76.5">
    </label>
    <div class="sheet-actions">
      <button class="btn-cancel" onclick="closeSheet()">Later</button>
      <button class="btn-save" onclick="submitDailyWeight()">Log weight</button>
    </div>
    <button class="reminder-disable" onclick="dismissWeightReminder()">Don't remind me daily</button>`;
  document.getElementById('overlay').classList.add('show');
  setTimeout(()=>document.getElementById('dailyWeightInput')?.focus(),250);
}
async function submitDailyWeight(){
  const input=document.getElementById('dailyWeightInput');
  const kg=Number(input&&input.value);
  if(!(kg>=30&&kg<=300)) return toast('Enter a valid weight');
  const r=await fetch('/api/confirm',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({type:'weight',weight_kg:kg,notes:'daily check-in'})});
  const d=await r.json();
  if(!r.ok||!d.ok) return toast(d.error||'Could not log weight');
  closeSheet(); toast('Weight logged · trend updated'); refreshToday();
}
function dismissWeightReminder(){
  localStorage.setItem('pulse-weight-reminder-disabled','1');
  closeSheet(); toast('Daily reminder disabled');
}
function toggleWeightReminder(){
  const disabled=localStorage.getItem('pulse-weight-reminder-disabled')==='1';
  if(disabled) localStorage.removeItem('pulse-weight-reminder-disabled');
  else localStorage.setItem('pulse-weight-reminder-disabled','1');
  toast(disabled?'Daily reminder enabled':'Daily reminder disabled');
  loadProgress();
}

async function loadSuggestions(force=false){
  const card=document.getElementById('suggestCard');
  const list=document.getElementById('suggestList');
  const cacheKey='pulse-suggestions-daily';
  let d=null;
  if(!force){
    try{
      const cached=JSON.parse(localStorage.getItem(cacheKey));
      if(cached&&cached.day===localDay()) d=cached.data;
    }catch(e){}
  }
  try{
    if(!d){
      const r=await fetch('/api/suggest');
      if(!r.ok) throw new Error('suggestions failed');
      d=await r.json();
      localStorage.setItem(cacheKey,JSON.stringify({day:localDay(),data:d}));
    }
    if(!d.suggestions||!d.suggestions.length){card.style.display='none';return;}
    list.innerHTML=d.suggestions.map(s=>`
      <div class="suggest-item">
        <div class="suggest-name">${esc(s.name)}</div>
        <div class="suggest-macros">${s.calories}kcal · ${s.protein}p · ${s.carbs}c · ${s.fat}f</div>
        <div class="suggest-reason">${esc(s.reason||'')}</div>
        <button class="suggest-log" onclick="quickLog('${jsStr(s.name)}',${s.calories},${s.protein},${s.carbs},${s.fat})">Log this</button>
      </div>
    `).join('');
    card.style.display='block';
  }catch(e){card.style.display='none';}
}

async function quickLog(name,cal,p,c,f){
  try{
    const r=await fetch('/api/log',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text:name,skip_clarification:true})});
    if(!r.ok){toast('Failed to log — try again');return;}
    refreshToday(); toast('Logged '+name);
  }catch(e){toast('Failed to log — check connection');}
}
function renderToday(d){
  const prevStreak = window._lastStreak || 0;
  document.getElementById('streakNum').textContent=d.streak;
  // streak bump when it goes up
  if(d.streak > prevStreak && prevStreak > 0){
    const pill=document.getElementById('streakPill');
    pill.classList.remove('bump'); void pill.offsetWidth; pill.classList.add('bump');
  }
  window._lastStreak = d.streak;
  const t=d.totals;
  // ring — animate number + arc, and color by how close to the calorie limit
  const target=d.cal_target||2000;
  const pct=Math.min(t.calories/target,1);
  const ratio=t.calories/target;
  const circ=2*Math.PI*52;
  const ringEl=document.getElementById('calRing');
  const svg=document.getElementById('ringSvg');
  ringEl.classList.remove('state-ok','state-warn','state-over');
  svg.classList.remove('glow-ok','glow-warn','glow-over');
  if(ratio>1.0){ ringEl.classList.add('state-over'); svg.classList.add('glow-over'); }
  else if(ratio>=0.85){ ringEl.classList.add('state-warn'); svg.classList.add('glow-warn'); }
  else { ringEl.classList.add('state-ok'); svg.classList.add('glow-ok'); }
  requestAnimationFrame(()=>{ ringEl.style.strokeDashoffset=circ*(1-pct); });
  animateCount('calNow', t.calories);
  document.getElementById('calTarget').textContent='/ '+target+' kcal';
  // macro bars scaled to your goal targets when set
  setBar('p',t.protein,d.protein_target||150,d.protein_target);
  setBar('c',t.carbs,d.carb_target||300,d.carb_target);
  setBar('f',t.fat,d.fat_target||80,d.fat_target);
  // water card
  const wtr=d.water_total||0, wtg=d.water_target||2500;
  animateCount('waterTot', wtr);
  document.getElementById('waterFill').style.width=Math.min(wtr/wtg*100,100)+'%';
  document.getElementById('waterSub').textContent=(wtr/1000).toFixed(1)+' / '+(wtg/1000)+' L';
  // manually recorded daily steps
  if(!stepsState.saving){
    stepsState.saved=Number(d.steps_today||0);
    stepsState.target=Number(d.step_target||5000);
  }
  renderStepsCard();
  // list
  const list=document.getElementById('todayList');
  let html='';
  d.foods.forEach(f=>{
    html+=`<div class="entry" onclick='openEdit("food",${f.id},${jsObj(f)})'><div class="emoji">🍽</div><div class="body">
      <div class="t">${esc(f.item_name)}</div>
      <div class="s">${f.calories} kcal · ${fmtMacro(f.protein_g)}p / ${fmtMacro(f.carbs_g)}c / ${fmtMacro(f.fat_g)}f</div></div>
      <div class="time">${f.ts.slice(11,16)}</div>
      <div class="del" onclick='event.stopPropagation();del("food",${f.id})'>✕</div></div>`;
  });
  d.workouts.forEach(w=>{
    const wt=w.weight_kg?`${w.weight_kg}kg `:'';
    html+=`<div class="entry" onclick='openEdit("workout",${w.id},${jsObj(w)})'><div class="emoji">🏋</div><div class="body">
      <div class="t">${esc(w.exercise_name)}</div>
      <div class="s">${wt}${w.sets}×${w.reps}</div></div>
      <div class="time">${w.ts.slice(11,16)}</div>
      <div class="del" onclick='event.stopPropagation();del("workout",${w.id})'>✕</div></div>`;
  });
  (d.waters||[]).forEach(w=>{
    html+=`<div class="entry"><div class="emoji">🚰</div><div class="body">
      <div class="t">Water</div>
      <div class="s">${w.ml} ml</div></div>
      <div class="time">${w.ts.slice(11,16)}</div>
      <div class="del" onclick='event.stopPropagation();del("water",${w.id})'>✕</div></div>`;
  });
  list.innerHTML=html||'<div class="empty">Nothing logged yet.<br>Tell me what you ate or lifted below 👇</div>';
}

/* ---------- WEEKLY STREAK ---------- */
function renderWeeklyStreak(d){
  document.getElementById('wkStreakCount').textContent=d.streak;
  document.getElementById('wkCalPct').textContent=d.cal_adherence+'%';
  document.getElementById('wkProtPct').textContent=d.protein_adherence+'%';
  document.getElementById('wkCalFill').style.width=Math.min(d.cal_adherence,100)+'%';
  document.getElementById('wkProtFill').style.width=Math.min(d.protein_adherence,100)+'%';
  document.getElementById('wkSub').textContent=d.days_logged+' of '+d.days_total+' days logged';
}
async function loadWeeklyStreak(){
  const d=await getJSON('/api/analytics/weekly');
  if(d) renderWeeklyStreak(d);
}

/* ---------- WEEKLY AI RECAP ---------- */
async function loadRecap(force=false){
  const card=document.getElementById('recapCard');
  const body=document.getElementById('recapBody');
  if(card.dataset.loading){return;}
  const cacheKey='pulse-recap-daily';
  if(!force){
    try{
      const cached=JSON.parse(localStorage.getItem(cacheKey));
      if(cached&&cached.day===localDay()){
        if(cached.data.recap){body.textContent=cached.data.recap;card.style.display='block';}
        else card.style.display='none';
        return;
      }
    }catch(e){}
  }
  card.dataset.loading='1';
  body.innerHTML='<span class="spinner" style="width:18px;height:18px"></span>';
  try{
    const d=await getJSON('/api/recap'+(force?'?refresh=1':''));
    if(d) localStorage.setItem(cacheKey,JSON.stringify({day:localDay(),data:d}));
    if(d && d.recap){
      body.textContent=d.recap;
      card.style.display='block';
    } else {
      card.style.display='none';
    }
  }catch(e){ card.style.display='none'; }
  delete card.dataset.loading;
}

/* ---------- WATER ---------- */
async function sendWater(ml){
  ml=+ml;
  if(!ml || ml<=0){toast('Enter a valid amount');return;}
  try{
    const r=await fetch('/api/water',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ml})});
    const d=await r.json();
    if(!r.ok){toast(d.error||'Could not log water');return;}
    if(d.ok){ toast(ml+' ml logged 💧'); refreshToday(); } else toast(d.error||'Could not log water');
  }catch(e){toast('Could not log water — check connection');}
}
async function undoWater(){
  try{
    const r=await fetch('/api/water',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({undo:true})});
    const d=await r.json();
    if(!r.ok){toast(d.error||'Nothing to undo');return;}
    if(d.ok){ toast('Removed last water'); refreshToday(); } else toast(d.error||'Nothing to undo');
  }catch(e){toast('Could not undo — check connection');}
}
function renderStepsCard(){
  const {saved,target,editing,saving}=stepsState;
  document.getElementById('stepsValue').textContent=saved.toLocaleString();
  document.getElementById('stepsSub').textContent=`Goal ${target.toLocaleString()}`;
  document.getElementById('stepsFill').style.width=Math.min(saved/target*100,100)+'%';
  const state=document.getElementById('stepsSaveState');
  state.textContent=saving?'Saving...':'today';
  state.classList.toggle('saving',saving);
  document.getElementById('stepsEditButton').hidden=editing||saving;
  document.getElementById('stepsEditButton').textContent=saved?'Edit':'Add steps';
  document.getElementById('stepsEditor').hidden=!editing;
  document.getElementById('stepsSaveButton').disabled=saving;
}
function editSteps(){
  if(stepsState.saving) return;
  stepsState.editing=true;
  const input=document.getElementById('stepsInput');
  input.value=stepsState.saved||'';
  renderStepsCard();
  requestAnimationFrame(()=>{input.focus();input.select();});
}
function cancelStepsEdit(){
  stepsState.editing=false;
  renderStepsCard();
}
function cacheSavedSteps(steps,target){
  try{
    const key='pulse-cache:/api/today';
    const cached=JSON.parse(localStorage.getItem(key));
    if(!cached) return;
    cached.steps_today=steps;
    cached.step_target=target;
    localStorage.setItem(key,JSON.stringify(cached));
  }catch(e){}
}
async function saveSteps(){
  const input=document.getElementById('stepsInput');
  const steps=Number(input.value);
  if(!Number.isInteger(steps)||steps<0||steps>100000) return toast('Enter steps from 0 to 100,000');
  if(stepsState.saving) return;
  const previous=stepsState.saved;
  stepsState.saved=steps;
  stepsState.editing=false;
  stepsState.saving=true;
  cacheSavedSteps(steps,stepsState.target);
  renderStepsCard();
  try{
    const r=await fetch('/api/steps',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({steps})});
    const d=await r.json();
    if(!r.ok||!d.ok) throw new Error(d.error||'Could not save steps');
    stepsState.target=Number(d.target||stepsState.target);
    stepsState.saving=false;
    cacheSavedSteps(steps,stepsState.target);
    renderStepsCard();
    toast(`${steps.toLocaleString()} steps saved`);
  }catch(e){
    stepsState.saved=previous;
    stepsState.saving=false;
    stepsState.editing=true;
    cacheSavedSteps(previous,stepsState.target);
    renderStepsCard();
    toast(e.message||'Could not save steps — check connection');
  }
}

/* ---------- RECENTS (quick re-log) ---------- */
async function loadRecents(){
  const r=await fetch('/api/recents'); const d=await r.json();
  const wrap=document.getElementById('recentsRow');
  if(!d.meals||!d.meals.length){ wrap.innerHTML=''; return; }
  const chips=d.meals.map(m=>
    `<div class="chip" onclick="relog('${jsStr(m.item_name)}')">
       <span class="plus">+</span>${esc(m.item_name)} <span class="kc">${m.calories}kc</span></div>`
  ).join('');
  wrap.innerHTML=`<div class="recents-wrap"><h2 class="eyebrow" style="margin:0 0 10px">Quick add — your usual</h2>
    <div class="recents-scroll">${chips}</div></div>`;
}
async function relog(name){
  const r=await fetch('/api/relog',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({item_name:name})});
  const d=await r.json();
  if(d.ok){ toast('Added ✅'); refreshToday(); successBurst(); }
}

/* ---------- EDIT ENTRY ---------- */
function openEdit(kind,id,row){
  const s=document.getElementById('editSheet');
  if(kind==='food'){
    s.innerHTML=`<div class="ph">✏️ Edit meal</div>
      <div class="ef">
        <div class="ef-row full"><label>Meal</label><input id="e_item" value="${esc(row.item_name)}"></div>
        <div class="ef-row"><label>Calories</label><input id="e_cal" type="number" value="${row.calories}"></div>
        <div class="ef-row"><label>Protein (g)</label><input id="e_p" type="number" value="${row.protein_g}"></div>
        <div class="ef-row"><label>Carbs (g)</label><input id="e_c" type="number" value="${row.carbs_g}"></div>
        <div class="ef-row"><label>Fat (g)</label><input id="e_f" type="number" value="${row.fat_g}"></div>
      </div>
      <div class="sheet-actions">
        <button class="btn-cancel" onclick="closeEdit()">Cancel</button>
        <button class="btn-save" onclick="saveEdit('food',${id})">Save changes</button></div>`;
  } else {
    s.innerHTML=`<div class="ph">✏️ Edit workout</div>
      <div class="ef">
        <div class="ef-row full"><label>Exercise</label><input id="e_item" value="${esc(row.exercise_name)}"></div>
        <div class="ef-row"><label>Weight (kg)</label><input id="e_w" type="number" value="${row.weight_kg}"></div>
        <div class="ef-row"><label>Sets</label><input id="e_s" type="number" value="${row.sets}"></div>
        <div class="ef-row"><label>Reps</label><input id="e_r" type="number" value="${row.reps}"></div>
      </div>
      <div class="sheet-actions">
        <button class="btn-cancel" onclick="closeEdit()">Cancel</button>
        <button class="btn-save" onclick="saveEdit('workout',${id})">Save changes</button></div>`;
  }
  document.getElementById('editOverlay').classList.add('show');
}
function closeEdit(){ document.getElementById('editOverlay').classList.remove('show'); }
async function saveEdit(kind,id){
  let fields={};
  if(kind==='food'){
    fields={item_name:gval('e_item'),calories:+gval('e_cal'),protein_g:+gval('e_p'),
      carbs_g:+gval('e_c'),fat_g:+gval('e_f')};
  } else {
    fields={exercise_name:gval('e_item'),weight_kg:+gval('e_w'),sets:+gval('e_s'),reps:+gval('e_r')};
  }
  try{
    const r=await fetch('/api/edit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,id,fields})});
    if(!r.ok){toast('Update failed — try again');return;}
    closeEdit(); toast('Updated ✅'); refreshToday();
  }catch(e){toast('Update failed — check connection');}
}
function gval(id){ return document.getElementById(id).value; }
function setBar(k,val,max,target){
  document.getElementById(k+'Fill').style.width=Math.min(val/max*100,100)+'%';
  const el=document.getElementById(k+'Val');
  const suffix = target ? `/${target}g` : `g`;
  const from=parseInt(el.textContent)||0;
  if(from===val){ el.textContent=val+suffix; return; }
  const dur=800, start=performance.now();
  function step(now){
    const p=Math.min((now-start)/dur,1), eased=1-Math.pow(1-p,3);
    el.textContent=Math.round(from+(val-from)*eased)+suffix;
    if(p<1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}
async function del(kind,id){
  try{
    const r=await fetch('/api/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,id})});
    if(!r.ok){toast('Remove failed — try again');return;}
    toast('Removed'); refreshToday();
  }catch(e){toast('Remove failed — check connection');}
}

/* ---------- ANALYTICS ---------- */
async function loadAnalytics(days){
  currentDays=days;
  document.querySelectorAll('.range button').forEach(b=>b.classList.toggle('active',+b.dataset.days===days));
  const d=await getJSON('/api/analytics?days='+days);
  if(!d) return;
  document.getElementById('aAvgCal').textContent=d.avg_cal;
  document.getElementById('aWorkouts').textContent=d.total_workouts;
  document.getElementById('aStreak').textContent=d.streak;
  drawLine('calChart',d.labels,[{label:'kcal',data:d.calories,color:'#c4ff4d',fill:true}]);
  drawLine('macroChart',d.labels,[
    {label:'protein',data:d.protein,color:'#c4ff4d'},
    {label:'carbs',data:d.carbs,color:'#4dd8ff'},
    {label:'fat',data:d.fat,color:'#ffb04d'}]);
  drawDonut('donutChart',d.macro_split);
  drawBar('volChart',d.labels,d.workout_volume);
  drawLine('weightChart',d.labels,[{label:'kg',data:d.weights,color:'#4dd8ff',fill:true,spanGaps:true}]);
  loadWeekly();
}
async function loadWeekly(){
  const d=await getJSON('/api/weekly?days=7');
  if(!d) return;
  const el=document.getElementById('weeklyBox');
  const wc=d.weight_change;
  const wcHtml = wc==null
    ? `<div class="wk-item"><b>—</b><span>weight delta (log 2+)</span></div>`
    : `<div class="wk-item ${wc<=0?'wk-good':'wk-bad'}"><b>${wc>0?'+':''}${wc} kg</b><span>weight change</span></div>`;
  const top=(d.top_meals||[]).map(m=>
    `<span class="wk-chip">${esc(m.item_name)} ×${m.count}</span>`).join('')||
    `<span class="wk-chip" style="opacity:.5">no meals logged</span>`;
  el.innerHTML=`<div class="wk-grid">
      <div class="wk-item"><b>${d.avg_cal}</b><span>avg kcal/day</span></div>
      <div class="wk-item"><b>${d.workouts}</b><span>workouts</span></div>
      <div class="wk-item"><b>${(d.water_ml/1000).toFixed(1)}L</b><span>water this week</span></div>
      ${wcHtml}
    </div>
    <div style="font-size:11px;color:var(--muted);margin-top:12px">Most logged meals</div>
    <div class="wk-top">${top}</div>`;
}
const gridColor='rgba(255,255,255,.05)', tickColor='#8fa38f';
function baseOpts(){return{responsive:true,maintainAspectRatio:false,
  interaction:{mode:'index',intersect:false},
  plugins:{legend:{display:false},
    tooltip:{enabled:true,backgroundColor:'rgba(22,30,22,0.95)',titleColor:'#c4ff4d',
      bodyColor:'#eef5ee',borderColor:'rgba(255,255,255,0.1)',borderWidth:1,
      padding:10,cornerRadius:12,displayColors:true,boxPadding:4,
      titleFont:{family:'Space Grotesk',weight:600},bodyFont:{family:'Inter'}}},
  scales:{x:{grid:{color:gridColor},ticks:{color:tickColor,maxTicksLimit:6,font:{size:10}}},
          y:{grid:{color:gridColor},ticks:{color:tickColor,font:{size:10}},beginAtZero:true}}};}
function drawLine(id,labels,series){
  if(charts[id])charts[id].destroy();
  charts[id]=new Chart(document.getElementById(id),{type:'line',
    data:{labels,datasets:series.map(s=>({label:s.label,data:s.data,borderColor:s.color,
      backgroundColor:s.fill?s.color+'22':'transparent',fill:!!s.fill,tension:.35,
      borderWidth:2,pointRadius:0,spanGaps:!!s.spanGaps}))},
    options:baseOpts()});
}
function drawBar(id,labels,data){
  if(charts[id])charts[id].destroy();
  charts[id]=new Chart(document.getElementById(id),{type:'bar',
    data:{labels,datasets:[{data,backgroundColor:'#7fbf2a',borderRadius:4}]},
    options:baseOpts()});
}
function drawDonut(id,split){
  if(charts[id])charts[id].destroy();
  charts[id]=new Chart(document.getElementById(id),{type:'doughnut',
    data:{labels:['Protein','Carbs','Fat'],
      datasets:[{data:[split.protein,split.carbs,split.fat],
        backgroundColor:['#c4ff4d','#4dd8ff','#ffb04d'],borderWidth:0}]},
    options:{responsive:true,maintainAspectRatio:false,cutout:'62%',
      plugins:{legend:{position:'bottom',labels:{color:'#eaf3ea',padding:14,font:{size:12}}}}}});
}

/* ---------- EXPORT ---------- */
async function exportCSV(kind){
  const r=await fetch('/api/export?kind='+kind);
  if(!r.ok){ toast('Export failed'); return; }
  const blob=await r.blob();
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  const cd=r.headers.get('Content-Disposition')||'';
  a.download=(cd.match(/filename=([^;]+)/)||[])[1]||('pulse-'+kind+'.csv');
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(a.href);
  toast('Exported '+kind+' ✅');
}

async function downloadBackup(){
  const r=await fetch('/api/backup');
  if(!r.ok) return toast('Backup failed');
  const blob=await r.blob(), a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  const cd=r.headers.get('Content-Disposition')||'';
  a.download=(cd.match(/filename=([^;]+)/)||[])[1]||'pulse-backup.json';
  document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(a.href);
  toast('Full backup downloaded');
}
async function restoreBackup(input){
  const file=input.files&&input.files[0]; input.value=''; if(!file) return;
  let backup;
  try{backup=JSON.parse(await file.text());}catch(e){return toast('Invalid backup file');}
  if(backup.format!=='pulse-backup') return toast('Not a Pulse backup');
  if(!confirm('Replace all Pulse data with this backup? This cannot be undone.')) return;
  try{
    const r=await fetch('/api/backup',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({confirm:'REPLACE',backup})});
    const d=await r.json(); if(!r.ok||!d.ok) return toast(d.error||'Restore failed');
    Object.keys(localStorage).filter(k=>k.startsWith('pulse-cache:')).forEach(k=>localStorage.removeItem(k));
    toast('Backup restored'); setTimeout(()=>location.reload(),700);
  }catch(e){toast('Restore failed — check connection');}
}

/* ---------- GOAL ---------- */
let goalTargets=null;
async function openGoal(){
  const r=await fetch('/api/goal'); const d=await r.json();
  if(d.goal){
    gv('gWeight',(d.targets&&d.targets.weight)||d.goal.start_weight);
    gv('gHeight',d.goal.height_cm); gv('gAge',d.goal.age);
    gv('gSex',d.goal.sex); gv('gActivity',d.goal.activity); gv('gObjective',d.goal.objective);
    gv('gSteps',d.goal.step_target||5000);
  }else{
    gv('gSteps',5000);
  }
  previewTargets();
  document.getElementById('goalOverlay').classList.add('show');
}
function closeGoal(){ document.getElementById('goalOverlay').classList.remove('show'); }
function gv(id,val){ if(val!=null)document.getElementById(id).value=val; }
['gWeight','gHeight','gAge','gSex','gActivity','gObjective'].forEach(id=>{
  document.addEventListener('input',e=>{ if(e.target.id===id) previewTargets(); });
});
async function previewTargets(){
  const body={weight_kg:val('gWeight'),height_cm:val('gHeight'),age:val('gAge'),
    sex:val('gSex'),activity:val('gActivity'),objective:val('gObjective')};
  if(!body.weight_kg||!body.height_cm||!body.age){document.getElementById('targetPreview').style.display='none';return;}
  const r=await fetch('/api/preview_targets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){document.getElementById('targetPreview').style.display='none';return;}
  const t=await r.json();
  document.getElementById('tpCal').textContent=t.calories;
  document.getElementById('tpP').textContent=t.protein+'g';
  document.getElementById('tpC').textContent=t.carbs+'g';
  document.getElementById('tpF').textContent=t.fat+'g';
  const obj=val('gObjective');
  const notes={cut_steady:'Steady fat loss ~0.4kg/week — best for keeping muscle.',
    cut_fast:'Faster cut ~0.7kg/week — more aggressive.',
    maintain:'Hold your current weight.',lean_bulk:'Slow muscle gain ~0.25kg/week.'};
  document.getElementById('tpNote').textContent=
    `Maintenance ≈ ${t.tdee} kcal. ${notes[obj]||''}`;
  document.getElementById('targetPreview').style.display='block';
}
async function saveGoal(){
  const body={weight_kg:val('gWeight'),height_cm:val('gHeight'),age:val('gAge'),
    sex:val('gSex'),activity:val('gActivity'),objective:val('gObjective'),
    step_target:val('gSteps')};
  if(!body.weight_kg||!body.height_cm||!body.age){toast('Fill weight, height & age');return;}
  if(!body.step_target||body.step_target<1||body.step_target>100000){toast('Enter a step goal from 1 to 100,000');return;}
  const r=await fetch('/api/goal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json();
  if(d.ok){ closeGoal(); toast('Goal saved 🎯'); refreshToday(); }
  else toast(d.error||'Could not save');
}
function val(id){ const v=document.getElementById(id).value; return v===''?null:v; }

/* ---------- UTIL ---------- */
function animateCount(id, to){
  const el=document.getElementById(id); if(!el) return;
  const from=parseInt(el.textContent)||0;
  if(from===to){ el.textContent=to; return; }
  const dur=700, start=performance.now();
  function step(now){
    const p=Math.min((now-start)/dur,1);
    const eased=1-Math.pow(1-p,3);
    el.textContent=Math.round(from+(to-from)*eased);
    if(p<1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
/* Safe embedding inside a JS string that lives in an HTML attribute:
   single quotes become \u0027 (entity-decoding never touches it). */
function jsStr(s){return esc(s).replace(/\\/g,'\\u005c').replace(/'/g,'\\u0027');}
/* JSON object for inline onclick: escape ' as \u0027 so apostrophes in
   names can't break the single-quoted attribute / JS string. */
function jsObj(o){return JSON.stringify(o)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
  .replace(/"/g,'&quot;').replace(/'/g,'\\u0027');}
let toastTimer;
function toast(m){const t=document.getElementById('toast');t.textContent=m;t.classList.add('show');
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.classList.remove('show'),2200);}
document.getElementById('overlay').addEventListener('click',e=>{if(e.target.id==='overlay')closeSheet();});
document.getElementById('editOverlay').addEventListener('click',e=>{if(e.target.id==='editOverlay')closeEdit();});
document.getElementById('goalOverlay').addEventListener('click',e=>{if(e.target.id==='goalOverlay')closeGoal();});

checkAuth();

/* ---------- BARCODE SCANNER ---------- */
let _barcodeScanner=null, _barcodeProduct=null;
function startBarcode(){
  document.getElementById('barcodeOverlay').classList.add('show');
  const res=document.getElementById('barcodeResult');
  res.innerHTML='<div style="color:var(--muted);font-size:13px">Starting camera…</div>';
  _clearScanner();
  if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){
    res.innerHTML='<div style="color:var(--danger);font-size:13px">Camera not supported here — type the barcode number below 👇</div>';
    return;
  }
  // Ask permission inside the tap gesture so iOS/Android show the prompt reliably.
  navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'}})
    .then(stream=>{
      stream.getTracks().forEach(t=>t.stop());
      loadBarcodeLib(()=>setTimeout(()=>initBarcodeScanner(),150));
    })
    .catch(()=>{
      res.innerHTML='<div style="color:var(--danger);font-size:13px">Camera blocked or unavailable — type the barcode number below 👇</div>';
    });
}
function loadBarcodeLib(cb){
  if(window.Html5QrcodeScanner){ cb(); return; }
  const s=document.createElement('script');
  s.src='https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js';
  s.onload=cb;
  s.onerror=()=>{ document.getElementById('barcodeResult').innerHTML=
    '<div style="color:var(--danger);font-size:13px">Scanner failed to load — type the barcode number below 👇</div>'; };
  document.head.appendChild(s);
}
function initBarcodeScanner(){
  if(!document.getElementById('barcodeOverlay').classList.contains('show')) return;
  const res=document.getElementById('barcodeResult');
  try{
    _barcodeScanner=new Html5QrcodeScanner("barcodeReader",{fps:10,qrbox:{width:280,height:120}});
    _barcodeScanner.render(code=>lookupBarcode(code), err=>{
      if(/NotFound|NotAllowed|NotReadable|Overconstrained|SecurityError/i.test(err||'')){
        res.innerHTML='<div style="color:var(--danger);font-size:13px">Can\'t access camera — type the barcode number below instead 👇</div>';
      }
    });
  }catch(e){
    res.innerHTML='<div style="color:var(--danger);font-size:13px">Camera failed to start — type the barcode number below 👇</div>';
  }
}
function stopBarcode(){
  document.getElementById('barcodeOverlay').classList.remove('show');
  _clearScanner();
}
function _clearScanner(){
  if(_barcodeScanner){ _barcodeScanner.clear().catch(()=>{}); _barcodeScanner=null; }
  const el=document.getElementById('barcodeReader');
  if(el) el.innerHTML='';
}
function manualBarcodeLookup(){
  const inp=document.getElementById('barcodeManual');
  const code=inp.value.trim(); inp.value='';
  if(!/^\d{8,14}$/.test(code)){ toast('Enter 8–14 digit barcode number'); return; }
  lookupBarcode(code);
}
function _sanitizeBarcode(raw){
  if(!raw) return '';
  let s=String(raw).trim();
  // If scanner returned a URL (some barcodes encode URLs), extract the digits.
  const m=s.match(/(\d{8,14})/);
  if(m) return m[1];
  // Strip anything that isn't a digit.
  s=s.replace(/\D/g,'');
  return s;
}
async function lookupBarcode(code){
  code=_sanitizeBarcode(code);
  if(!code){ toast('Invalid barcode'); return; }
  _clearScanner();
  const res=document.getElementById('barcodeResult');
  res.innerHTML='<div style="color:var(--muted);font-size:13px">Looking up '+esc(code)+'...</div>';
  try{
    const r=await fetch('/api/barcode/'+code);
    const d=await r.json();
    if(!r.ok||!d.found){res.innerHTML='<div style="color:var(--danger);font-size:13px">'+esc(d.error||('Not found (HTTP '+r.status+')'))+'</div>';return;}
    _barcodeProduct=d;
    res.innerHTML=`
      <div class="barcode-info">
        <div class="barcode-name">${esc(d.name)}${d.brand?' <span style="color:var(--muted)">('+esc(d.brand)+')</span>':''}</div>
        <div class="barcode-serving">${esc(d.serving_size)} · ${esc(d.basis)}</div>
        <div class="barcode-macros">${d.calories}kcal · ${d.protein}p · ${d.carbs}c · ${d.fat}f</div>
        <button class="suggest-log" onclick="logBarcode()">Log exact values</button>
      </div>`;
  }catch(e){res.innerHTML='<div style="color:var(--danger);font-size:13px">Network error — try again</div>';}
}
async function logBarcode(){
  const d=_barcodeProduct; if(!d) return toast('Scan the product again');
  const name=[d.brand,d.name].filter(Boolean).join(' ');
  const body={type:'food',item_name:name,calories:d.calories,protein_g:d.protein,
    carbs_g:d.carbs,fat_g:d.fat,fiber_g:d.fiber,sugar_g:d.sugar,
    source:'barcode',matched_food:d.name,serving_g:d.serving_g,qty:1,
    confidence_notes:`barcode package data: ${d.serving_size}`,_raw:`barcode: ${d.name}`};
  try{
    const r=await fetch('/api/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const saved=await r.json();
    if(!r.ok||!saved.ok) return toast(saved.error||'Failed to log');
    if(d.serving_g>0){
      fetch('/api/custom_food',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({...body,name,serving_g:d.serving_g})}).catch(()=>{});
    }
    stopBarcode(); refreshToday(); toast('Logged exact package values');
  }catch(e){toast('Failed to log');}
}

/* ---------- PWA ---------- */
if('serviceWorker' in navigator){ navigator.serviceWorker.register('/sw.js').catch(()=>{}); }
