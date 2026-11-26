"""The History page (served at /history): everything heard and done, searchable, with one-click
teaching ("what I said there means this command")."""

HISTORY_PAGE = r"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>History</title>
<style>
:root{--bg:#f4f4f1;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#e2e2dc;--ok:#2f7d4f;--warn:#b3541e;--acc:#2a5db0}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1f1f1d;--ink:#ecebe6;--mute:#9b9a94;--line:#33332f;--ok:#5cb27f;--warn:#e08a52;--acc:#7aa5ee}}
*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
header{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;padding:16px 20px}
h1{font-size:20px;margin:0}h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:0 0 10px}
main{display:grid;gap:14px;padding:0 20px 24px;grid-template-columns:minmax(0,2.2fr) minmax(260px,1fr)}
@media (max-width:860px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
button,select,input{font:inherit;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);padding:5px 9px}
button{cursor:pointer}a{color:var(--acc)}.mute{color:var(--mute);font-size:13px}
.e{display:grid;grid-template-columns:110px 1fr;gap:4px 12px;padding:9px 0;border-bottom:1px solid var(--line)}.e:last-child{border:0}
.when{color:var(--mute);font-size:13px}.what b{font-weight:600}.ok{color:var(--ok)}.bad{color:var(--warn)}
.teach{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}.row{display:flex;justify-content:space-between;gap:8px;padding:5px 0;border-bottom:1px solid var(--line)}
.row:last-child{border:0}
</style>
<header><h1>History</h1><div style="display:flex;gap:8px;align-items:center">
<input id="q" placeholder="Search" oninput="clearTimeout(window.qt);window.qt=setTimeout(load,250)">
<button onclick="if(confirm('Delete all history?'))api('/api/history/clear',{}).then(load)">Clear</button>
<a href="/">Dashboard</a></div></header>
<main>
<section class="card"><h2>What was heard and done</h2><div id="off" class="mute" hidden>History is off. It's on while
`simulate` runs; to keep it on the real house add <code>history: {enabled: true}</code> to config.yaml.</div>
<div id="list"></div></section>
<div>
<section class="card"><h2>Taught</h2><div id="aliases"></div>
<div class="mute" style="margin-top:8px">When the house mishears you, pick what you meant next to it, and it
will understand those words from then on. Saying "yes" to "did you say ...?" teaches it too.</div></section>
</div></main>
<script>
let D={entries:[],commands:[],aliases:[]};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
 const j=await r.json().catch(()=>({}));if(r.status==401){location.href='/';return null}if(!r.ok){alert(j.error||r.status);return null}return j}
async function load(){if(document.activeElement&&document.activeElement.tagName==='SELECT')return;  // don't redraw mid-choice
 const j=await api('/api/history?q='+encodeURIComponent(q.value));if(!j)return;D=j;draw()}
const pct=c=>c==null?'':` <span class=mute>${Math.round(c*100)}% sure</span>`;
const who=e=>[e.room,e.person].filter(Boolean).map(esc).join(' · ');
function teachBox(e){if(!e.heard)return'';
 return `<div class="teach"><select data-id="${esc(e.id)}"><option value="">${e.kind=='command'?'Wrong? It meant…':'It meant…'}</option>`+
  D.commands.map(([n,p])=>`<option value="${esc(n)}">${esc(p)} (${esc(n.replace(/_/g,' '))})</option>`).join('')+
  `</select></div>`}
function line(e){
 const via=e.via&&e.via!=='voice'?` <span class=mute>(${esc(e.via)})</span>`:'';
 switch(e.kind){
  case 'command':return `Heard “${esc(e.heard)}”${pct(e.confidence)} → <b class=ok>${esc(e.matched)}</b>${via}${teachBox(e)}`;
  case 'none':return `Heard “${esc(e.heard)}”${pct(e.confidence)} → <span class=bad>no command</span>${via}${teachBox(e)}`;
  case 'unclear':return `Heard “${esc(e.heard)}”${pct(e.confidence)} → <span class=bad>too unsure to act</span>${teachBox(e)}`;
  case 'asked':return `Heard “${esc(e.heard)}” → asked “did you say ${esc(e.matched)}?”`;
  case 'confirm':return `“Did you say ${esc(e.matched)}?” → <b>${esc(e.answer)}</b>`;
  case 'ignored':return `Heard “${esc(e.heard)}” → <span class=mute>said the name, but not a task or a question: ignored</span>${teachBox(e)}`;
  case 'not_for_me':return `Answered “${esc(e.heard)}” → <span class=mute>told “I wasn't talking to you”: learned</span>`;
  case 'stranger':return `A voice it doesn't know said “${esc(e.heard)}” → <span class=bad>needs the name</span> (${esc(e.matched)})${teachBox(e)}`;
  case 'ai':return `Asked: <b>${esc(e.question)}</b>${via}<br><span class=mute>Answer:</span> ${esc(e.answer)}`;
  default:return esc(JSON.stringify(e))}}
function draw(){off.hidden=D.enabled;
 list.innerHTML=D.entries.map(e=>`<div class="e"><div class="when">${esc(e.time.slice(5,16))}<br>${who(e)}</div><div class="what">${line(e)}</div></div>`).join('')
  ||(D.enabled?'<div class=mute>Nothing yet. Say something.</div>':'');
 aliases.innerHTML=D.aliases.map((a,i)=>`<div class="row"><span>“${esc(a.heard)}” → <b>${esc(a.command.replace(/_/g,' '))}</b></span><button data-un="${i}">Forget</button></div>`).join('')
  ||'<div class=mute>Nothing taught yet.</div>'}
list.addEventListener('change',async ev=>{const s=ev.target;if(s.tagName!=='SELECT'||!s.value)return;
 const r=await api('/api/history/teach',{id:s.dataset.id,command:s.value});s.blur();if(r)load()});
aliases.addEventListener('click',async ev=>{const i=ev.target.dataset.un;if(i==null)return;
 if(await api('/api/history/unteach',{heard:D.aliases[+i].heard}))load()});
let liveOn=false,lt=null;
if(window.EventSource){const es=new EventSource('/api/live');es.onopen=()=>{liveOn=true};es.onerror=()=>{liveOn=false};
 es.onmessage=m=>{let e;try{e=JSON.parse(m.data)}catch(_){return}
  if(e.kind==='history'||!e.live){clearTimeout(lt);lt=setTimeout(load,200)}}}
load();setInterval(()=>{if(!liveOn)load()},5000);
</script>
"""
