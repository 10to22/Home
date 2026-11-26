"""The main dashboard page (served at /). Plain HTML + JS, no build step."""

DASHBOARD = r"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Home</title>
<style>
:root{--bg:#f4f4f1;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#e2e2dc;--ok:#2f7d4f;--warn:#b3541e;--acc:#2a5db0;--accbg:#e7eefb}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1f1f1d;--ink:#ecebe6;--mute:#9b9a94;--line:#33332f;--ok:#5cb27f;--warn:#e08a52;--acc:#7aa5ee;--accbg:#22304a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;padding:16px 20px}
h1{font-size:20px;margin:0}h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:0 0 10px}
main{display:grid;gap:14px;padding:0 20px 24px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}.wide{grid-column:1/-1}
.row{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 0;border-bottom:1px solid var(--line)}
.row:last-child{border-bottom:0}.mute{color:var(--mute);font-size:13px}
button,select,input{font:inherit;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);padding:6px 10px}
button{cursor:pointer}button.primary{background:var(--acc);border-color:var(--acc);color:#fff}
a{color:var(--acc)}.pill{font-size:12px;padding:2px 8px;border-radius:99px;border:1px solid var(--line)}
.locked,.good{color:var(--ok)}.unlocked,.bad{color:var(--warn)}
.chips{display:flex;flex-wrap:wrap;gap:6px}.x{border:0;background:none;color:var(--mute);padding:0 4px}
img.scan{width:44px;height:44px;object-fit:cover;border-radius:6px;vertical-align:middle;margin-right:8px}
form{display:flex;gap:6px;margin-top:8px}form input{flex:1;min-width:0}
.cams{display:grid;gap:8px;grid-template-columns:repeat(auto-fill,minmax(260px,1fr))}
.cam{position:relative;background:#000;border-radius:10px;overflow:hidden;aspect-ratio:4/3}
.cam img{width:100%;height:100%;object-fit:cover;display:block}.cam span{position:absolute;left:8px;bottom:6px;color:#fff;font-size:12px;text-shadow:0 1px 3px #000}
svg.plan{width:100%;height:auto;background:var(--bg);border-radius:10px;touch-action:none}
svg.plan rect.room{fill:var(--card);stroke:var(--line);stroke-width:.4}svg.plan rect.room.busy{fill:var(--accbg);stroke:var(--acc)}
svg.plan text{font-size:2.2px;fill:var(--ink)}svg.plan text.small{font-size:1.7px;fill:var(--mute)}
svg.plan rect.handle{fill:var(--acc)}
.bar{height:6px;background:var(--accbg);border-radius:3px;overflow:hidden;margin-top:3px}.bar i{display:block;height:100%;background:var(--acc)}
.diary{white-space:pre-wrap}
</style>
<header><h1>Home</h1><div class="chips">
<a href="/meals" class="pill" style="padding:6px 10px">Meal planner</a> <a href="/alarms" class="pill" style="padding:6px 10px">Alarms</a> <a href="/history" class="pill" style="padding:6px 10px">History</a> <a href="/voice" class="pill" style="padding:6px 10px">Voice</a>
<span id="alarmTop"></span><span id="freakTop"></span>
<select id="mode" onchange="post('/api/mode',{mode:this.value})"></select>
<select id="aiMode" title="AI: smart = best answers, fast = quicker" style="display:none" onchange="post('/api/ai',{mode:this.value})"><option value="smart">AI: smart</option><option value="fast">AI: fast</option></select>
<button id="priv" onclick="post('/api/privacy',{on:!S.private,minutes:60})"></button></div></header>
<main>
<section class="card wide" id="camCard" style="display:none"><h2>Cameras <button class="x" onclick="toggleCams()" id="camToggle">pause</button></h2><div class="cams" id="cams"></div></section>
<section class="card wide"><h2>House <button class="x" id="editBtn" onclick="toggleEdit()">edit layout</button></h2><svg class="plan" id="plan" viewBox="0 0 100 60"></svg><div class="mute" id="planHelp" style="display:none">Drag rooms to move them, drag the blue corner to resize, then <button onclick="savePlan()">Save layout</button></div></section>
<section class="card"><h2>Doors</h2><div id="doors"></div></section>
<section class="card"><h2>Routines</h2><div class="chips" id="routines"></div></section>
<section class="card" id="voiceCard" style="display:none"><h2>Voices</h2><div id="voiceList"></div>
<div class="row"><select id="voiceWho"></select><button onclick="post('/api/voice/learn',{person:voiceWho.value,sentences:6})">Learn my voice</button></div>
<div class="mute">Press it, then say any six sentences near a microphone.</div></section>
<section class="card" id="simCard" style="display:none"><h2>Laptop (testing)</h2>
<div class="row" id="simMicRow"><span>Microphone is in</span><select id="simMic" onchange="post('/api/sim',{mic_room:this.value})"></select></div>
<div class="row" id="simCamRow"><span>Webcam is</span><select id="simCam" onchange="post('/api/sim',{camera:this.value})"></select></div>
<img id="simPrev" style="width:100%;border-radius:8px;margin-top:8px;display:none" alt="webcam">
<div id="simNote" class="mute"></div></section>
<section class="card"><h2>Say or ask</h2>
<form onsubmit="heard(event)"><input id="cmdText" placeholder="Type a command, e.g. lights on"><select id="cmdRoom"></select><button>Run</button></form>
<form onsubmit="say(event)"><input id="sayText" placeholder="Dinner's ready!"><select id="sayRoom"></select><button>Say</button></form>
<form id="askForm" onsubmit="ask(event)"><input id="askText" placeholder="Ask the house..."><button class="primary">Ask</button></form>
<div id="answer" class="mute"></div><div id="live" class="mute" style="margin-top:4px"></div></section>
<section class="card" id="weatherCard" style="display:none"><h2>Weather</h2><div id="weather"></div></section>
<section class="card"><h2>Who's home</h2><div id="people"></div></section>
<div id="rooms" style="display:contents"></div>
<section class="card" id="energyCard" style="display:none"><h2>Energy this week</h2><div id="energy"></div></section>
<section class="card"><h2>Packages</h2><div id="packages"></div>
<form onsubmit="addPkg(event,this)"><input name="number" placeholder="Tracking number"><input name="name" placeholder="What is it?" style="max-width:110px"><button>Track</button></form></section>
<section class="card"><h2>Countdowns</h2><div id="countdowns"></div>
<form onsubmit="addCd(event,this)"><input name="name" placeholder="Beach trip"><input name="date" type="date" style="max-width:150px"><button>Add</button></form></section>
<section class="card" id="sleepCard" style="display:none"><h2>Sleep</h2><div id="sleep"></div></section>
<section class="card" id="petsCard" style="display:none"><h2>Pets</h2><div id="pets"></div></section>
<section class="card"><h2>Devices</h2><div id="devices"></div></section>
<section class="card" id="diaryCard" style="display:none"><h2>Today's diary</h2><div id="diary" class="diary"></div></section>
<section class="card wide"><h2>Recent activity</h2><div id="events"></div></section>
</main>
<script>
let S={},A=[],editing=false,layout=null,camsOn=true;const qs=new URLSearchParams(location.search);
if(qs.get('token')){document.cookie='ph_token='+encodeURIComponent(qs.get('token'))+';max-age=31536000;path=/';history.replaceState(null,'','/')}
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// buttons call closures by index, so no data from the house is ever written into JS code
const btn=(label,fn,cls='')=>{A.push(fn);return `<button class="${cls}" onclick="A[${A.length-1}]()">${label}</button>`};
async function post(url,body){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const j=await r.json().catch(()=>({}));if(!r.ok)alert(j.error||r.status);load();return j}
function say(e){e.preventDefault();const t=sayText.value.trim();if(!t)return;const room=sayRoom.value;post('/api/say',room?{text:t,room}:{text:t,everywhere:true});sayText.value=''}
async function heard(e){e.preventDefault();const t=cmdText.value.trim();if(!t)return;
 const j=await post('/api/heard',{text:t,room:cmdRoom.value||null});answer.textContent=j.assistant||(j.matched?'Ran: '+j.matched.replace(/_/g,' '):j.hint||'');cmdText.value=''}
async function ask(e){e.preventDefault();const q=askText.value.trim();if(!q)return;answer.textContent='Thinking...';const j=await post('/api/ask',{question:q});answer.textContent=j.answer||j.error||'';askText.value=''}
function addPkg(e,f){e.preventDefault();if(f.number.value.trim())post('/api/package',{number:f.number.value,name:f.name.value});f.reset()}
function addCd(e,f){e.preventDefault();if(f.name.value.trim()&&f.date.value)post('/api/countdown',{name:f.name.value,date:f.date.value});f.reset()}
function addItem(e,f){e.preventDefault();const i=S.instances[+f.dataset.i],v=f.item.value.trim();if(v)post('/api/list',{person:i.id,list:f.list.value||(i.main?'shopping':'todo'),add:[v]});f.item.value=''}
function when(iso){const d=new Date(iso);return d.toLocaleString([], {weekday:'short',hour:'numeric',minute:'2-digit'})}
function toggleCams(){camsOn=!camsOn;camToggle.textContent=camsOn?'pause':'resume'}
function drawCams(){const c=S.cameras||[];camCard.style.display=c.length?'':'none';
 const key=c.map(x=>x.name+x.online).join('|');if(cams.dataset.key==key)return;cams.dataset.key=key;
 cams.innerHTML=c.map(x=>`<div class="cam"><img data-cam="${esc(x.name)}" alt=""><span>${esc(x.name)}${x.online?'':' · offline'}</span></div>`).join('');refreshCams(true)}
// live view: refresh each picture ~2x a second (works in every browser; pauses when the tab is hidden)
function refreshCams(force){if(!force&&(!camsOn||document.hidden))return;document.querySelectorAll('img[data-cam]').forEach(img=>{
 if(img.dataset.busy)return;img.dataset.busy=1;const n=new Image();n.onload=n.onerror=()=>{if(n.naturalWidth)img.src=n.src;delete img.dataset.busy};
 n.src='/api/camera?name='+encodeURIComponent(img.dataset.cam)+'&t='+Date.now()})}
setInterval(refreshCams,500);
// ---------- floor plan ----------
function toggleEdit(){editing=!editing;editBtn.textContent=editing?'cancel':'edit layout';planHelp.style.display=editing?'':'none';layout=editing?JSON.parse(JSON.stringify(S.floorplan)):null;drawPlan()}
function savePlan(){post('/api/floorplan',{rooms:layout});editing=false;editBtn.textContent='edit layout';planHelp.style.display='none';layout=null}
function drawPlan(){const fp=layout||S.floorplan||{},R=(S.rooms||{}).rooms||{},C=S.climate||{},tags=(S.rooms||{}).tags||{};
 const tagsIn={};for(const[t,w]of Object.entries(tags))if(w)(tagsIn[w.room]=tagsIn[w.room]||[]).push(t);
 const ns='http://www.w3.org/2000/svg';plan.innerHTML='';
 for(const[name,[x,y,w,h]]of Object.entries(fp)){const g=document.createElementNS(ns,'g');const r=R[name]||{};
  const rect=document.createElementNS(ns,'rect');rect.setAttribute('class','room'+(r.occupied||(r.people||[]).length?' busy':''));
  Object.entries({x,y,width:w,height:h,rx:1}).forEach(([k,v])=>rect.setAttribute(k,v));g.appendChild(rect);
  const lines=[name,...(r.people||[]).map(p=>'• '+p),C[name]&&C[name].temperature!=null?`${C[name].temperature}°${C[name].humidity!=null?' · '+C[name].humidity+'%':''}`:'',(tagsIn[name]||[]).map(t=>'🔑 '+t).join(' ')].filter(Boolean);
  lines.forEach((t,i)=>{const tx=document.createElementNS(ns,'text');tx.setAttribute('x',x+1.5);tx.setAttribute('y',y+3.2+i*2.6);if(i)tx.setAttribute('class','small');tx.textContent=t;g.appendChild(tx)});
  if(editing){g.style.cursor='move';g.onpointerdown=ev=>drag(ev,name,'move');const hd=document.createElementNS(ns,'rect');
   Object.entries({x:x+w-2,y:y+h-2,width:2,height:2,class:'handle'}).forEach(([k,v])=>hd.setAttribute(k,v));
   hd.onpointerdown=ev=>{ev.stopPropagation();drag(ev,name,'size')};g.appendChild(hd)}
  plan.appendChild(g)}
 const bottom=Math.max(20,...Object.values(fp).map(r=>r[1]+r[3]+2));plan.setAttribute('viewBox',`0 0 100 ${editing?60:Math.min(60,bottom)}`)}
function drag(ev,name,kind){const pt=plan.createSVGPoint(),m=plan.getScreenCTM().inverse(),start=(()=>{pt.x=ev.clientX;pt.y=ev.clientY;return pt.matrixTransform(m)})(),orig=[...layout[name]];
 const move=e=>{pt.x=e.clientX;pt.y=e.clientY;const p=pt.matrixTransform(m),dx=p.x-start.x,dy=p.y-start.y,r=layout[name];
  if(kind=='move'){r[0]=Math.max(0,Math.min(100-r[2],orig[0]+dx));r[1]=Math.max(0,Math.min(60-r[3],orig[1]+dy))}else{r[2]=Math.max(8,orig[2]+dx);r[3]=Math.max(6,orig[3]+dy)}drawPlan()};
 const up=()=>{removeEventListener('pointermove',move);removeEventListener('pointerup',up)};addEventListener('pointermove',move);addEventListener('pointerup',up)}
// ---------- load ----------
function fillSel(el,opts,val){const key=opts.map(o=>o[0]).join('|');
 if(el.dataset.opts!==key){el.innerHTML=opts.map(([v,l])=>`<option value="${esc(v)}">${esc(l)}</option>`).join('');el.dataset.opts=key}
 if(document.activeElement!==el)el.value=val??''}
function drawVoices(){const v=S.voices;voiceCard.style.display=v?'':'none';if(!v)return;
 voiceList.innerHTML=Object.entries(v.people).map(([p,n])=>`<div class="row"><span>${esc(p)} <span class=mute>${n} samples</span></span>${btn('Forget',()=>{if(confirm('Forget '+p+"'s voice?"))post('/api/voice/forget',{person:p})})}</div>`).join('')
  ||'<div class=mute>No voices learned yet.</div>';
 if(v.learning)voiceList.innerHTML+=`<div class="mute">Learning ${esc(v.learning.person)}: say ${v.learning.left} more sentence${v.learning.left==1?'':'s'}</div>`;
 fillSel(voiceWho,v.names.map(n=>[n.toLowerCase(),n]),voiceWho.value||(v.names[0]||'').toLowerCase())}
function drawSim(){const m=S.sim;simCard.style.display=m?'':'none';if(!m)return;
 simMicRow.style.display=m.mic?'':'none';fillSel(simMic,m.rooms.map(r=>[r,r]),m.mic_room);
 simCamRow.style.display=m.webcam?'':'none';fillSel(simCam,[['','off (unplugged)'],...m.cameras.map(c=>[c,'the '+c+' camera'])],m.camera);
 const show=m.webcam&&m.camera;simPrev.style.display=show?'':'none';
 if(show)simPrev.src='/api/camera?name='+encodeURIComponent(m.camera)+'&t='+Date.now();
 simNote.textContent=m.error?'Webcam problem: '+m.error:(!m.mic&&!m.webcam?'Start simulate with --mic and/or --webcam to place them here.':'')}
async function load(){
 const r=await fetch('/api/status');
 if(r.status==401){const t=prompt('Dashboard token');if(t){document.cookie='ph_token='+encodeURIComponent(t)+';max-age=31536000;path=/';load()}return}
 S=await r.json();A=[];
 mode.innerHTML=S.modes.map(m=>`<option ${m==S.mode?'selected':''}>${m}</option>`).join('');
 aiMode.style.display=S.ai?'':'none';if(S.ai&&document.activeElement!==aiMode){aiMode.value=S.ai.mode;aiMode.title='AI model: '+S.ai.model}
 priv.textContent=S.private?'Mics paused - resume':'Pause mics 1h';
 freakTop.innerHTML=(S.freak||[]).map(f=>`<span class="pill bad">freak mode: ${esc(f.room)}, ${f.minutes_left} min left</span> `+btn('End',()=>post('/api/freak',{action:'off',room:f.room}))).join(' ');
 alarmTop.innerHTML=S.alarm?`<span class="pill ${S.alarm=='disarmed'?'':'bad'}">alarm ${esc(S.alarm)}</span> `+(S.alarm=='disarmed'?btn('Arm',()=>post('/api/alarm',{action:'arm'})):btn('Disarm',()=>post('/api/alarm',{action:'disarm'}))):'';
 drawCams();drawSim();drawVoices();if(!editing)drawPlan();
 doors.innerHTML=Object.entries(S.doors).map(([d,s])=>`<div class="row"><span>${esc(d)} <span class="pill ${s}">${s}</span></span><span>
  ${btn('Lock',()=>post('/api/door',{door:d,state:'locked'}))}${S.api_unlock?btn('Unlock',()=>post('/api/door',{door:d,state:'unlocked'})):''}</span></div>`).join('')||'<span class=mute>No doors configured</span>';
 routines.innerHTML=S.routines.map(n=>btn(esc(n.replace(/_/g,' ')),()=>post('/api/routine',{name:n}))).join('')||'<span class=mute>None yet</span>';
 const rooms=[...new Set(S.instances.flatMap(i=>i.rooms))];
 sayRoom.innerHTML='<option value="">Everywhere</option>'+rooms.map(r=>`<option>${esc(r)}</option>`).join('');
 if(!cmdRoom.options.length||cmdRoom.dataset.n!=rooms.length){cmdRoom.dataset.n=rooms.length;cmdRoom.innerHTML='<option value="">(no room)</option>'+rooms.map(r=>`<option>${esc(r)}</option>`).join('')}
 askForm.style.display=S.assistant?'flex':'none';
 weatherCard.style.display=S.weather?'':'none';
 if(S.weather)weather.innerHTML=`<div>${esc(S.weather.today)}</div><div class="mute" style="margin-top:6px">${esc(S.weather.tomorrow)}</div>${S.weather.needs.length?`<div style="margin-top:6px">Take: <b>${S.weather.needs.map(esc).join(', ')}</b></div>`:''}`;
 people.innerHTML=Object.entries(S.presence).map(([p,v])=>`<div class="row"><span>${esc(p)}</span><span class="pill ${v.state=='home'?'locked':''}">${v.state} since ${esc(v.since.slice(5,16))}</span></div>`).join('')||'<span class=mute>No presence info yet</span>';
 document.getElementById('rooms').innerHTML=S.instances.map(i=>`<section class="card"><h2>${esc(i.name)}${i.main?' (main)':''}${i.dnd?' · do not disturb':''}${i.quiet?' · quiet hours':''}</h2>
  ${i.agenda.length?i.agenda.map(a=>`<div class="row"><span>${a.kind=='alarm'?'⏰':a.kind=='timer'?'⏱':a.kind=='action'?'⚙':'🔔'} ${esc(a.text||a.label||a.kind)}${a.repeat!='none'?` <span class=mute>(${esc(a.repeat)})</span>`:''}</span><span class=mute>${when(a.due)} ${btn('✕',()=>post('/api/agenda/cancel',{person:i.id,id:a.id}),'x')}</span></div>`).join(''):'<div class=mute>No reminders</div>'}
  ${Object.entries(i.lists).map(([n,items])=>`<div style="margin-top:10px"><b>${esc(n)}</b><div class=chips style="margin-top:4px">${items.map(it=>`<span class=pill>${esc(it)} ${n=='shopping'?btn('✓',()=>post('/api/list/bought',{item:it}),'x'):''}${btn('✕',()=>post('/api/list',{person:i.id,list:n,remove:[it]}),'x')}</span>`).join('')||'<span class=mute>empty</span>'}</div></div>`).join('')}
  <form data-i="${S.instances.indexOf(i)}" onsubmit="addItem(event,this)">
  <input name="item" placeholder="Add to list"><input name="list" placeholder="${i.main?'shopping':'todo'}" style="max-width:90px"><button>Add</button></form></section>`).join('');
 energyCard.style.display=S.energy?'':'none';
 if(S.energy){const E=S.energy,max=Math.max(...Object.values(E.people),0.01);
  energy.innerHTML=`<div>${E.kwh} kWh · ${esc(E.currency)}${E.cost}</div>`+Object.entries(E.people).map(([p,v])=>`<div style="margin-top:6px">${esc(p)} <span class=mute>${v} kWh</span><div class=bar><i style="width:${100*v/max}%"></i></div></div>`).join('')+
  `<div class=mute style="margin-top:8px">${Object.entries(E.plugs).map(([n,p])=>`${esc(n)}: ${p.state=='running'?'running':p.watts+' W'}`).join(' · ')}</div>`}
 packages.innerHTML=(S.packages||[]).map(p=>`<div class="row"><span>📦 ${esc(p.name)} <span class=mute>${esc(p.words||p.status)}${p.eta?' · '+esc(p.eta):''}</span></span>${btn('✕',()=>post('/api/package',{action:'remove',number:p.number}),'x')}</div>`).join('')||'<span class=mute>Nothing on the way</span>';
 countdowns.innerHTML=(S.countdowns||[]).map(c=>`<div class="row"><span>${esc(c.name)}</span><span><b>${c.days}</b> <span class=mute>day${c.days==1?'':'s'}</span> ${btn('✕',()=>post('/api/countdown',{action:'remove',name:c.name}),'x')}</span></div>`).join('')||'<span class=mute>No countdowns</span>';
 const sl=Object.entries(S.sleep||{}).filter(([p,v])=>v);sleepCard.style.display=sl.length?'':'none';
 sleep.innerHTML=sl.map(([p,v])=>`<div class="row"><span>${esc(p)}</span><span>${v.hours} h <span class=mute>${esc(v.in)}–${esc(v.out)}</span></span></div>`).join('');
 const pe=Object.entries(S.pets||{});petsCard.style.display=pe.length?'':'none';
 pets.innerHTML=pe.map(([p,t])=>`<div class="row"><span>🐾 ${esc(p)}</span><span class=mute>${esc(t)}</span></div>`).join('');
 diaryCard.style.display=S.diary&&S.diary.today?'':'none';if(S.diary)diary.textContent=S.diary.today||'';
 const devs=(S.satellites||[]).map(s=>`<div class="row"><span>mic: ${esc(s.room)}</span><span class="pill ${s.online?'':'bad'}">${s.online?(s.muted?'muted':'online'):'offline'}${s.speaker?' · speaker':''}</span></div>`);
 devices.innerHTML=(S.offline.length?`<div class="row bad">Offline: ${S.offline.map(esc).join(', ')}</div>`:'')+devs.join('')||'<span class=mute>All good</span>';
 events.innerHTML=S.events.map(e=>`<div class="row"><span>${e.photo?`<img class=scan src="/api/photo?f=${encodeURIComponent(e.photo)}">`:''}<b>${esc(e.kind)}</b> ${esc(e.name||e.description||e.detected||e.result||e.door||e.node||e.person||e.appliance||e.mode||e.message||e.text||e.question||'')} ${e.result&&e.name?esc(e.result):''}</span><span class=mute>${esc(e.time.slice(5,16))}</span></div>`).join('');
}
let liveOn=false,liveT=null,tick=0;
function heardWords(e){  // words it wasn't sure of are underlined, so you can see where it mishears
 if(!(e.words||[]).length)return esc(e.text);
 return e.words.map(w=>w.conf<0.6?`<span title="${Math.round(w.conf*100)}% sure" style="text-decoration:underline wavy var(--warn)">${esc(w.word)}</span>`:esc(w.word)).join(' ')}
function liveLine(e){const r=e.room||((e.rooms||[])[0])||'';const at=r&&r!=='hub'?' in '+esc(r):'';
 const t={hearing:`Hearing${at}: <i>${esc(e.text)}…</i>`,
  heard:`Heard${at}${e.person?' from '+esc(e.person):''}: “${heardWords(e)}”`+(e.confidence!=null?` <span class=mute>(${Math.round(e.confidence*100)}% sure)</span>`:''),
  voice_learning:`Learning ${esc(e.person)}'s voice: ${e.done} of ${e.total} sentences`+(e.done>=e.total?' — done':''),
  thinking:`Thinking${at}: ${esc(e.question)}`,searching:`Searching the web: ${esc(e.query)}`,
  saying:`Saying${at}: ${esc(e.text)}`}[e.kind];
 if(t)live.innerHTML=t}
function startLive(){if(!window.EventSource)return;const es=new EventSource('/api/live');
 es.onopen=()=>{liveOn=true};es.onerror=()=>{liveOn=false};
 es.onmessage=m=>{let e;try{e=JSON.parse(m.data)}catch(_){return}
  if(e.live){liveLine(e);return}
  clearTimeout(liveT);liveT=setTimeout(()=>{if(!editing)load()},150)}}  // something happened: show it now
load();startLive();
setInterval(()=>{if(!editing&&(!liveOn||++tick%6==0))load()},5000);  // a backup, in case the live link drops
</script></html>"""
