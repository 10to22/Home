"""The House page (served at /house): the student-house features at a glance, with buttons.
People and what the house knows about them, the party, study sessions, door lights, exams, the
house meeting and its agenda, who's walking home, the shower record, the quiz and the radio."""

HOUSE_PAGE = r"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>House</title>
<style>
:root{--bg:#f4f4f1;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#e2e2dc;--ok:#2f7d4f;--warn:#b3541e;--acc:#2a5db0}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1f1f1d;--ink:#ecebe6;--mute:#9b9a94;--line:#33332f;--ok:#5cb27f;--warn:#e08a52;--acc:#7aa5ee}}
*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
header{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;padding:16px 20px}
h1{font-size:20px;margin:0}h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:0 0 10px}
main{display:grid;gap:14px;padding:0 20px 24px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
button,select,input{font:inherit;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);padding:5px 9px}
button{cursor:pointer}a{color:var(--acc)}.mute{color:var(--mute);font-size:13px}.ok{color:var(--ok)}.bad{color:var(--warn)}
.row{display:flex;justify-content:space-between;gap:8px;align-items:center;padding:6px 0;border-bottom:1px solid var(--line)}
.row:last-child{border:0}.chips{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0}.chip{border:1px solid var(--line);border-radius:99px;padding:2px 9px;font-size:13px}
.chip button{border:0;padding:0 0 0 6px;background:none;color:var(--mute)}form{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.btns{display:flex;gap:6px;flex-wrap:wrap}
</style>
<header><h1>House</h1><a href="/">Dashboard</a></header>
<main>
<section class="card"><h2>People</h2><div id="people"></div>
<form onsubmit="event.preventDefault();post('/api/house/people',{action:'remember',person:pp.value,fact:pf.value}).then(()=>{pf.value='';load()})">
<select id="pp"></select><input id="pf" placeholder="is vegetarian, hates coriander…" style="flex:1"><button>Add</button></form>
<div class="mute" style="margin-top:6px">Or just say it: “house, Parker's a vegetarian”. Diets and allergies go into the meal planner too.</div></section>

<section class="card"><h2>Party</h2><div id="party"></div><div class="btns" style="margin-top:8px">
<button onclick="act('party_start')">Start the party</button><button onclick="act('party_stop')">Party's over</button></div></section>

<section class="card"><h2>Study and doors</h2><div id="study"></div><div id="doors" style="margin-top:6px"></div></section>

<section class="card"><h2>Exams</h2><div id="exams"></div>
<form onsubmit="event.preventDefault();post('/api/house/exam',{person:ep.value,subject:es.value,at:ea.value.replace('T',' ')}).then(()=>{es.value='';load()})">
<select id="ep"></select><input id="es" placeholder="subject" style="width:110px"><input id="ea" type="datetime-local"><button>Add</button></form></section>

<section class="card"><h2>House meeting</h2><div id="meeting"></div><div class="btns" style="margin-top:8px">
<button onclick="act('meeting_book')">Find a time and book it</button><button onclick="act('meeting_cancel')">Cancel</button></div>
<div id="agenda" style="margin-top:8px"></div>
<form onsubmit="event.preventDefault();post('/api/house/agenda',{item:ai.value}).then(()=>{ai.value='';load()})">
<input id="ai" placeholder="add to the agenda" style="flex:1"><button>Add</button></form></section>

<section class="card"><h2>Walking home</h2><div id="walking"></div>
<form onsubmit="event.preventDefault();post('/api/walkhome',{action:'start',person:wp.value,minutes:+wm.value||0}).then(load)">
<select id="wp"></select><input id="wm" type="number" min="1" placeholder="minutes" style="width:90px"><button>Walking home</button></form>
<div class="mute" style="margin-top:6px">From anywhere: post “Parker walking home 20” to the house's ntfy topic (walk_home: topic).</div></section>

<section class="card"><h2>This week</h2><div id="week"></div><div class="btns" style="margin-top:8px">
<button onclick="act('quiz_start')">Start the quiz</button><button onclick="act('radio_on')">House Radio on</button>
<button onclick="act('radio_off')">Radio off</button><button onclick="act('windows_advice')">Open the windows?</button></div></section>
</main>
<script>
let D=null;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
 const j=await r.json().catch(()=>({}));if(r.status==401){location.href='/';return null}if(!r.ok){alert(j.error||r.status);return null}return j}
const post=(p,b)=>api(p,b);const act=a=>post('/api/house',{action:a}).then(load);
async function load(){if(document.activeElement&&['INPUT','SELECT'].includes(document.activeElement.tagName))return;
 const j=await api('/api/house');if(!j)return;D=j;draw()}
function opts(sel){const v=sel.value;sel.innerHTML=D.people.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');if(v)sel.value=v}
function draw(){
 [pp,ep,wp].forEach(opts);
 people.innerHTML=D.people.map(p=>`<div class="row"><b>${esc(p.name)}</b><div class="chips">${p.facts.map(f=>
  `<span class="chip">${esc(f)}<button title="Forget" data-p="${esc(p.id)}" data-f="${esc(f)}">×</button></span>`).join('')||'<span class=mute>nothing yet</span>'}</div></div>`).join('');
 const pa=D.party;party.innerHTML=pa?`<span class=ok>On.</span> Quiet time ${esc(pa.quiet_at)} (${pa.minutes_left} min)${pa.why?' – '+esc(pa.why):''}${pa.quieted?' · quiet now':''}`:'<span class=mute>No party on.</span>';
 study.innerHTML=D.study.map(s=>`<div class="row"><span>${esc(s.room)}</span><span>${esc(s.phase)}, ${s.minutes_left} min left · ${s.blocks} done</span></div>`).join('')||'<span class=mute>Nobody in study mode.</span>';
 doors.innerHTML=Object.entries(D.doors).map(([r,s])=>`<div class="row"><span>${esc(r)}</span><span class="${s=='free'?'ok':'bad'}">${esc(s)}</span></div>`).join('');
 exams.innerHTML=(D.exam_quiet?'<div class=bad>Exam quiet hours are on.</div>':'')+(D.exams.map(e=>`<div class="row"><span>${esc(e.person)}: ${esc(e.subject)}</span><span>${esc(e.at)}</span></div>`).join('')||'<span class=mute>No exams coming up.</span>');
 meeting.innerHTML=D.meeting?`Booked: <b>${esc(D.meeting.at.replace('T',' '))}</b> in the ${esc(D.meeting.room)}`:'<span class=mute>No meeting booked.</span>';
 agenda.innerHTML=[...D.agenda.map(a=>`<span class="chip">${esc(a)}</span>`),...D.talking_points.map(a=>`<span class="chip mute">${esc(a)}</span>`)].join(' ')||'<span class=mute>Agenda is empty.</span>';
 walking.innerHTML=D.walking.map(w=>`<div class="row"><span>${esc(w.name)}${w.from?' from '+esc(w.from):''}</span><span class="${w.late?'bad':''}">due ${esc(w.eta)}${w.late?' – late':''}
  <button data-home="${esc(w.person)}">Home</button></span></div>`).join('')||'<span class=mute>Nobody on the way home.</span>';
 const sh=D.shower.record,lo=Object.entries(D.last_out);
 week.innerHTML=`<div class="row"><span>Longest shower</span><span>${sh?esc(sh.name)+', '+sh.minutes+' min':'–'}${D.shower.running?' · <span class=bad>someone\'s in there</span>':''}</span></div>`+
  `<div class="row"><span>Left a door unlocked (30 days)</span><span>${lo.map(([n,c])=>esc(n)+' ×'+c).join(', ')||'nobody'}</span></div>`+
  `<div class="row"><span>Quiz / radio</span><span>${D.quiz.running?'quiz on':'–'} · ${D.radio.on?'radio on':'radio off'}</span></div>`}
people.addEventListener('click',ev=>{const b=ev.target;if(!b.dataset.f)return;post('/api/house/people',{action:'forget',person:b.dataset.p,fact:b.dataset.f}).then(load)});
walking.addEventListener('click',ev=>{const p=ev.target.dataset.home;if(p)post('/api/walkhome',{action:'arrived',person:p}).then(load)});
let liveOn=false,lt=null;
if(window.EventSource){const es=new EventSource('/api/live');es.onopen=()=>{liveOn=true};es.onerror=()=>{liveOn=false};
 es.onmessage=m=>{let e;try{e=JSON.parse(m.data)}catch(_){return}if(!e.live){clearTimeout(lt);lt=setTimeout(load,300)}}}
load();setInterval(()=>{if(!liveOn)load()},10000);setInterval(load,60000);
</script>
"""
