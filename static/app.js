let pending=null, charts={};

/* ---------- AUTH ---------- */
async function checkAuth(){
  const r=await fetch('/api/me'); const d=await r.json();
  if(d.authed){ showApp(); } else { document.getElementById('login').style.display='flex'; }
}
async function doLogin(){
  const pass=document.getElementById('passInput').value;
  const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({passcode:pass})});
  const d=await r.json();
  if(d.ok){ showApp(); } else { document.getElementById('loginErr').textContent='Wrong passcode'; }
}
document.getElementById('passInput').addEventListener('keydown',e=>{if(e.key==='Enter')doLogin();});
function showApp(){
  document.getElementById('login').style.display='none';
  document.getElementById('appWrap').style.display='block';
  document.getElementById('dock').style.display='block';
  refreshToday();
}

/* ---------- TABS ---------- */
function switchTab(t){
  document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x.dataset.tab===t));
  document.querySelectorAll('.section').forEach(x=>x.classList.remove('active'));
  document.getElementById('tab-'+t).classList.add('active');
  if(t==='analytics') loadAnalytics(currentDays);
  if(t==='progress') loadProgress();
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
  const hero=document.getElementById('progHero');
  if(!d.current){
    hero.innerHTML='<div class="empty" style="padding:20px">Log your weight to start tracking progress.<br>Just type "I weigh 77kg" below.</div>';
    document.getElementById('paceBox').innerHTML='';
    if(charts['weightTrend'])charts['weightTrend'].destroy();
    return;
  }
  const lost=d.start!=null?(d.start-d.current).toFixed(1):0;
  hero.innerHTML=`<div class="cur">${d.current}<small>kg</small></div>
    <div class="sub">avg intake ${d.avg_cal} kcal/day${d.target_cal?` · target ${d.target_cal}`:''}</div>
    ${d.start!=null?`<div class="lost">${lost>0?'Down':'Up'} <b>${Math.abs(lost)}kg</b> from ${d.start}kg</div>`:''}`;
  // weight chart
  drawLine('weightTrend',d.weights.map(w=>w.day),[{label:'kg',data:d.weights.map(w=>w.kg),color:'#c4ff4d',fill:true,spanGaps:true}]);
  // pace
  const pace=document.getElementById('paceBox');
  if(d.rate_kg_per_week==null){
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
}

/* ---------- INPUT ---------- */
const ta=document.getElementById('msg');
ta.addEventListener('input',()=>{ta.style.height='auto';ta.style.height=Math.min(ta.scrollHeight,100)+'px';});
ta.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendText();}});

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

/* ---------- VOICE (Web Speech API — free, no server needed) ---------- */
let _recognition = null;
async function toggleMic(){
  const mic=document.getElementById('micBtn');
  if(_recognition){ _recognition.stop(); _recognition=null; return; }
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if(!SR){ toast('Speech recognition not supported in this browser'); return; }
  _recognition = new SR();
  _recognition.continuous = false;
  _recognition.interimResults = false;
  _recognition.lang = 'en-IN'; // English + Hindi auto-detect
  mic.classList.add('rec'); mic.textContent='⏹';
  _recognition.onresult = async (event) => {
    const text = event.results[0][0].transcript;
    _recognition = null; mic.classList.remove('rec'); mic.textContent='🎙';
    ta.value = text; await sendText();
  };
  _recognition.onend = () => { _recognition=null; mic.classList.remove('rec'); mic.textContent='🎙'; };
  _recognition.onerror = (e) => { _recognition=null; mic.classList.remove('rec'); mic.textContent='🎙';
    if(e.error!=='aborted') toast('Voice error: '+e.error); };
  try{ _recognition.start(); }catch(e){ toast('Could not start voice'); _recognition=null; mic.classList.remove('rec'); mic.textContent='🎙'; }
}

/* ---------- RESULT / PREVIEW ---------- */
function handleResult(d){
  if(d.type==='chat'){ showChat(d.reply); return; }
  // Smart clarification: ask one quick question to nail the portion
  if(d.type==='food' && d.needs_clarification && d.clarify_options && d.clarify_options.length){
    showClarify(d);
    return;
  }
  pending=d;
  const s=document.getElementById('sheet');
  if(d.type==='food'){
    s.innerHTML=`<div class="ph">🍽 ${esc(d.item_name)}</div>
      <div class="kcal">${d.calories} kcal</div>
      <div class="macros">
        <span>💪 ${d.protein_g}g protein</span><span>🍞 ${d.carbs_g}g carbs</span><span>🥑 ${d.fat_g}g fat</span>
        <span>🌾 ${d.fiber_g}g fiber</span><span>🍬 ${d.sugar_g}g sugar</span></div>
      ${d.confidence_notes?`<div class="note">${esc(d.confidence_notes)}</div>`:''}
      <div class="sheet-actions">
        <button class="btn-cancel" onclick="closeSheet()">Cancel</button>
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
    s.innerHTML=`<div class="ph">⚖ Body weight</div>
      <div class="kcal">${d.weight_kg} kg</div>
      ${d.notes?`<div class="note">${esc(d.notes)}</div>`:''}
      <div class="sheet-actions">
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
    `<button class="clarify-opt" onclick="answerClarify('${esc(o).replace(/'/g,"\\'")}')">${esc(o)}</button>`
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
  const r=await fetch('/api/clarify',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({original:clarifyCtx.original,question:clarifyCtx.question,answer,round:clarifyCtx.round})});
  const d=await r.json();
  handleResult(d); // may show a 2nd question, or the final confirm card
}
function skipClarify(){
  const d=clarifyCtx.fallback; d.needs_clarification=false;
  clarifyCtx=null; handleResult(d);
}

function showChat(reply){
  pending=null;
  document.getElementById('sheet').innerHTML=`<div class="chat-bubble">${esc(reply)}</div>
    <div class="sheet-actions"><button class="btn-save" onclick="closeSheet()">Got it</button></div>`;
  document.getElementById('overlay').classList.add('show');
}
function closeSheet(){ document.getElementById('overlay').classList.remove('show'); pending=null; }
async function confirmEntry(){
  if(!pending) return closeSheet();
  const r=await fetch('/api/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pending)});
  const d=await r.json();
  const wasWeight = pending && pending.type==='weight';
  const wasWater = pending && pending.type==='water';
  closeSheet();
  if(d.ok){
    toast(wasWeight ? 'Weight logged — targets updated 🎯' : (wasWater ? 'Logged 💧' : 'Logged ✅'));
    refreshToday();
    successBurst();
  }
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
  if(d) renderToday(d);
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
  // list
  const list=document.getElementById('todayList');
  let html='';
  d.foods.forEach(f=>{
    html+=`<div class="entry" onclick='openEdit("food",${f.id},${JSON.stringify(f).replace(/'/g,"&#39;")})'><div class="emoji">🍽</div><div class="body">
      <div class="t">${esc(f.item_name)}</div>
      <div class="s">${f.calories} kcal · ${f.protein_g}p / ${f.carbs_g}c / ${f.fat_g}f</div></div>
      <div class="time">${f.ts.slice(11,16)}</div>
      <div class="del" onclick='event.stopPropagation();del("food",${f.id})'>✕</div></div>`;
  });
  d.workouts.forEach(w=>{
    const wt=w.weight_kg?`${w.weight_kg}kg `:'';
    html+=`<div class="entry" onclick='openEdit("workout",${w.id},${JSON.stringify(w).replace(/'/g,"&#39;")})'><div class="emoji">🏋</div><div class="body">
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
  loadRecents();
}

/* ---------- WATER ---------- */
async function sendWater(ml){
  const r=await fetch('/api/water',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ml})});
  const d=await r.json();
  if(d.ok){ toast(ml+' ml logged 💧'); refreshToday(); } else toast(d.error||'Could not log water');
}
async function undoWater(){
  const r=await fetch('/api/water',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({undo:true})});
  const d=await r.json();
  if(d.ok){ toast('Removed last water'); refreshToday(); }
}

/* ---------- RECENTS (quick re-log) ---------- */
async function loadRecents(){
  const r=await fetch('/api/recents'); const d=await r.json();
  const wrap=document.getElementById('recentsRow');
  if(!d.meals||!d.meals.length){ wrap.innerHTML=''; return; }
  const chips=d.meals.map(m=>
    `<div class="chip" onclick="relog('${esc(m.item_name).replace(/'/g,"\\'")}')">
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
  await fetch('/api/edit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,id,fields})});
  closeEdit(); toast('Updated ✅'); refreshToday();
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
  await fetch('/api/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,id})});
  toast('Removed'); refreshToday();
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

/* ---------- GOAL ---------- */
let goalTargets=null;
async function openGoal(){
  const r=await fetch('/api/goal'); const d=await r.json();
  if(d.goal){
    gv('gWeight',(d.targets&&d.targets.weight)||d.goal.start_weight);
    gv('gHeight',d.goal.height_cm); gv('gAge',d.goal.age);
    gv('gSex',d.goal.sex); gv('gActivity',d.goal.activity); gv('gObjective',d.goal.objective);
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
    sex:val('gSex'),activity:val('gActivity'),objective:val('gObjective')};
  if(!body.weight_kg||!body.height_cm||!body.age){toast('Fill weight, height & age');return;}
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
let toastTimer;
function toast(m){const t=document.getElementById('toast');t.textContent=m;t.classList.add('show');
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.classList.remove('show'),2200);}
document.getElementById('overlay').addEventListener('click',e=>{if(e.target.id==='overlay')closeSheet();});
document.getElementById('editOverlay').addEventListener('click',e=>{if(e.target.id==='editOverlay')closeEdit();});
document.getElementById('goalOverlay').addEventListener('click',e=>{if(e.target.id==='goalOverlay')closeGoal();});

checkAuth();