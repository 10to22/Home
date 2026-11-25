"""The alarms page (served at /alarms): add, change, pause and test alarms, and watch the
make-sure-I'm-up ones while they're going."""

ALARMS_PAGE = r"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alarms</title>
<style>
:root{--bg:#f4f4f1;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#e2e2dc;--ok:#2f7d4f;--warn:#b3541e;--acc:#2a5db0;--accbg:#e7eefb}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1f1f1d;--ink:#ecebe6;--mute:#9b9a94;--line:#33332f;--ok:#5cb27f;--warn:#e08a52;--acc:#7aa5ee;--accbg:#22304a}}
*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
header{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;padding:16px 20px}
h1{font-size:20px;margin:0}h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:0 0 10px}
main{display:grid;gap:14px;padding:0 20px 24px;grid-template-columns:minmax(0,2fr) minmax(280px,1fr)}
@media (max-width:860px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
button,select,input{font:inherit;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);padding:6px 10px}
button{cursor:pointer}button.primary{background:var(--acc);border-color:var(--acc);color:#fff}a{color:var(--acc)}
.alarm{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--line)}
.alarm:last-child{border:0}.time{font-size:28px;font-weight:600;font-variant-numeric:tabular-nums;min-width:120px}
.off .time,.off .what{opacity:.45}.what{flex:1;min-width:200px}.mute{color:var(--mute);font-size:13px}
.acts{display:flex;gap:6px;flex-wrap:wrap}.ring{border-left:4px solid var(--warn);padding-left:10px;margin-bottom:10px}
.up{border-left-color:var(--ok)}.row{display:flex;justify-content:space-between;gap:8px;padding:5px 0;border-bottom:1px solid var(--line)}
.row:last-child{border:0}.stack>*+*{margin-top:10px}label{display:block}label.inline{display:inline-flex;gap:6px;align-items:center}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}.days{display:flex;gap:4px;flex-wrap:wrap}
.days label{display:inline-flex;gap:3px;align-items:center;border:1px solid var(--line);border-radius:99px;padding:2px 8px}
dialog{border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink);width:min(560px,94vw)}
dialog::backdrop{background:#0006}input[type=number]{width:80px}.switch{min-width:56px}
</style>
<header><h1>Alarms</h1><div style="display:flex;gap:8px;align-items:center">
<button class="primary" onclick="edit(null)">Add alarm</button><a href="/">Dashboard</a></div></header>
<main>
<div class="stack">
<section class="card" id="nowCard" hidden><h2>Going off now</h2><div id="now"></div></section>
<section class="card"><h2>Alarms</h2><div id="list"></div></section>
</div>
<div class="stack">
<section class="card"><h2>Recent mornings</h2><div id="recent"></div></section>
<section class="card"><h2>How "make sure I'm up" works</h2><div class="mute">
Saying "stop" while still in bed only quiets the alarm: it rings again after the re-ring time unless
you've got up. Up = out of bed for a bit (bed sensor), or moving in another room (motion sensors).
Snoozes are limited. Back in bed soon after getting up starts it again. After the second nag it turns
on the lights, and in the end it gives up and texts your phone.</div></section>
</div>
</main>
<dialog id="dlg"><form method="dialog" class="stack" onsubmit="save(event)">
<h2 id="dlgTitle">Alarm</h2>
<div class="grid2"><label>Whose <select id="fInst" onchange="rooms()"></select></label>
<label>Time <input id="fTime" type="time" required value="07:00"></label></div>
<div class="grid2"><label>Days <select id="fRepeat" onchange="custom()">
<option value="none">Once</option><option value="daily">Every day</option><option value="weekdays">Weekdays</option>
<option value="weekends">Weekends</option><option value="custom">Pick days</option></select></label>
<label>Room <select id="fRoom"></select></label></div>
<div class="days" id="fDays" hidden></div>
<label>Label <input id="fLabel" style="width:100%" placeholder="e.g. work, gym"></label>
<label class="inline"><input type="checkbox" id="fUp"> Make sure I'm actually up (watch the bed / motion sensors)</label>
<div id="upOpts" class="grid2">
<label>Snoozes allowed <input id="fSnoozes" type="number" min="0" max="10"></label>
<label>Snooze length (min) <input id="fSnoozeMin" type="number" min="1" max="30"></label>
<label>Ring again after "stop" in bed (min) <input id="fRecheck" type="number" min="1" max="30" step="0.5"></label>
<label>Out of bed this long = up (sec) <input id="fUpSec" type="number" min="10" max="600"></label>
<label>Back in bed within (min) = ring again <input id="fBack" type="number" min="0" max="60"></label>
<label>Give up and text me after (min) <input id="fGiveUp" type="number" min="5" max="180"></label>
</div>
<div id="sensorNote" class="mute"></div>
<div style="display:flex;gap:6px;justify-content:space-between"><span id="delWrap"></span>
<span><button type="button" onclick="dlg.close()">Cancel</button> <button class="primary">Save</button></span></div>
</form></dialog>
<script>
let D={alarms:[],sessions:[],recent:[],instances:[],defaults:{}},A=[],cur=null;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const btn=(label,fn,cls='')=>{A.push(fn);return `<button type="button" class="${cls}" onclick="A[${A.length-1}]()">${label}</button>`};
const DAYS=['mon','tue','wed','thu','fri','sat','sun'],DN={mon:'Mon',tue:'Tue',wed:'Wed',thu:'Thu',fri:'Fri',sat:'Sat',sun:'Sun'};
const RW={none:'Once',daily:'Every day',weekdays:'Weekdays',weekends:'Weekends',weekly:'Weekly'};
const repeatWords=r=>RW[r]||r.split(',').map(d=>DN[d]||d).join(', ');
const pl=(n,w)=>`${n} ${w}${n==1?'':'s'}`;
const t12=hm=>{let[h,m]=hm.split(':').map(Number);const ap=h<12?'AM':'PM';h=h%12||12;return `${h}:${String(m).padStart(2,'0')} ${ap}`};
async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
 const j=await r.json().catch(()=>({}));if(r.status==401){location.href='/';return null}if(!r.ok){alert(j.error||r.status);return null}return j}
async function load(){const j=await api('/api/alarms');if(j){D=j;draw()}}
function draw(){A=[];
 const live=D.sessions.filter(s=>s.state!=='done');nowCard.hidden=!live.length;
 now.innerHTML=live.map(s=>{const st=s.state=='ringing'?'Ringing':s.state=='quiet'?'Quiet for now: still in bed, it will ring again':'Up';
  const bed=s.in_bed===null?'':s.in_bed?' · in bed':' · out of bed';
  return `<div class="ring ${s.state=='up'?'up':''}"><b>${esc(s.name)}</b> <span class=mute>(${esc(s.room||'')})</span><br>${st}${bed}
  <div class=mute>${s.minutes} min since it went off · ${pl(s.nags,'nag')} · snoozed ${s.snoozes} of ${s.snooze_limit}</div>
  ${s.state!='up'?`<div class="acts" style="margin-top:6px">${btn('Stop (like saying stop)',()=>act('stop',s.room))}${btn('Snooze',()=>act('snooze',s.room))}</div>`:''}</div>`}).join('');
 list.innerHTML=D.alarms.map(a=>{const w=a.wake;
  const watch=w.make_sure_up?(a.sensor=='none'?' · make sure up: <b>no sensors</b>, stops when told':` · makes sure you're up (${a.sensor=='bed'?'bed sensor':'motion'}) · ${pl(w.snooze_limit,'snooze')}`):'';
  return `<div class="alarm ${a.off?'off':''}"><div class="time">${t12(a.time)}</div>
  <div class="what"><b>${esc(a.name)}</b>${a.label?' · '+esc(a.label):''} <span class=mute>· ${esc(a.room||'')}</span><br>
  <span class=mute>${repeatWords(a.repeat)}${a.snoozed?' (snoozed)':''}${a.off?' · paused':' · next '+esc(a.next)}${watch}</span></div>
  <div class="acts">${btn(a.off?'Turn on':'Pause',()=>toggle(a),'switch')}${btn('Edit',()=>edit(a))}${btn('Ring now',()=>ringNow(a))}</div></div>`}).join('')
  ||'<div class=mute>No alarms yet. Add one, or say "set an alarm for 7".</div>';
 recent.innerHTML=D.recent.slice().reverse().map(e=>e.kind=='wake.up'
  ?`<div class=row><span>${esc(e.name||e.person)} got up in <b>${e.minutes} min</b></span><span class=mute>${pl(e.snoozes,'snooze')}, ${pl(e.nags,'nag')} · ${esc(e.time.slice(5,16))}</span></div>`
  :`<div class=row><span>${esc(e.name||e.person)}: <b>never got up</b> (gave up after ${e.minutes} min)</span><span class=mute>${esc(e.time.slice(5,16))}</span></div>`).join('')
  ||'<div class=mute>Nothing yet.</div>';
}
function rooms(sel){const i=D.instances.find(i=>i.id==fInst.value)||{rooms:[]};
 fRoom.innerHTML=(i.rooms.length?i.rooms:['']).map(r=>`<option value="${esc(r)}">${esc(r||'(the hub)')}</option>`).join('');
 if(sel)fRoom.value=sel;
 sensorNote.textContent=i.sensor=='bed'?'Uses '+i.name+"'s bed sensor.":i.sensor=='motion'?'No bed sensor for '+i.name+': uses motion in the other rooms.':'No bed or motion sensors yet: "make sure I\'m up" can\'t check, so stop will just stop.'}
function custom(days){fDays.hidden=fRepeat.value!='custom';
 fDays.innerHTML=DAYS.map(d=>`<label><input type=checkbox value="${d}" ${days&&days.includes(d)?'checked':''}>${DN[d]}</label>`).join('')}
function edit(a){cur=a;const w=a?a.wake:D.defaults;
 fInst.innerHTML=D.instances.map(i=>`<option value="${esc(i.id)}">${esc(i.name)}</option>`).join('');
 fInst.value=a?a.instance:(D.instances.find(i=>!i.main)||D.instances[0]||{}).id;rooms(a&&a.room);
 fTime.value=a?a.time:'07:00';const r=a?a.repeat:'weekdays';
 fRepeat.value=RW[r]&&r!='weekly'?r:'custom';custom(RW[r]?[]:r.split(','));
 fLabel.value=a?a.label:'';fUp.checked=!!w.make_sure_up;fSnoozes.value=w.snooze_limit;fSnoozeMin.value=w.snooze_minutes;
 fRecheck.value=w.recheck_minutes;fUpSec.value=w.up_seconds;fBack.value=w.back_to_bed_minutes;fGiveUp.value=w.give_up_minutes;
 dlgTitle.textContent=a?'Edit alarm':'New alarm';delWrap.innerHTML='';
 if(a){const b=document.createElement('button');b.type='button';b.textContent='Delete';  // not via A[]: the list redraws
  b.onclick=async()=>{if(confirm('Delete this alarm?')){await api('/api/alarms/delete',{instance:a.instance,id:a.id});dlg.close();load()}};
  delWrap.appendChild(b)}
 dlg.showModal()}
async function save(e){e.preventDefault();
 let repeat=fRepeat.value;if(repeat=='custom'){repeat=[...fDays.querySelectorAll('input:checked')].map(x=>x.value).join(',')||'none'}
 const body={instance:fInst.value,time:fTime.value,repeat,label:fLabel.value,room:fRoom.value||null,
  wake:{make_sure_up:fUp.checked,snooze_limit:+fSnoozes.value,snooze_minutes:+fSnoozeMin.value,recheck_minutes:+fRecheck.value,
        up_seconds:+fUpSec.value,back_to_bed_minutes:+fBack.value,give_up_minutes:+fGiveUp.value}};
 if(cur){body.id=cur.id;body.instance=cur.instance;if(fInst.value!=cur.instance){await api('/api/alarms/delete',{instance:cur.instance,id:cur.id});delete body.id;body.instance=fInst.value}}
 if(await api('/api/alarms/save',body)){dlg.close();load()}}
async function toggle(a){await api('/api/alarms/save',{instance:a.instance,id:a.id,off:!a.off});load()}
async function ringNow(a){if(await api('/api/alarms/ring',{instance:a.instance,id:a.id}))setTimeout(load,1500)}
async function act(what,room){await api('/api/alarms/'+what,{room});setTimeout(load,500)}
let liveOn=false,liveT=null;
if(window.EventSource){const es=new EventSource('/api/live');es.onopen=()=>{liveOn=true};es.onerror=()=>{liveOn=false};
 es.onmessage=m=>{let e;try{e=JSON.parse(m.data)}catch(_){return}
  if(!e.live&&/^(wake|agenda|alarm|voice)/.test(e.kind||'')){clearTimeout(liveT);liveT=setTimeout(load,150)}}}
load();setInterval(()=>{if(!liveOn)load()},3000);setInterval(load,20000);
</script>
"""
