"""The meal planner page (served at /meals)."""

MEALS_PAGE = r"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Meal planner</title>
<style>
:root{--bg:#f4f4f1;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#e2e2dc;--ok:#2f7d4f;--warn:#b3541e;--acc:#2a5db0;--accbg:#e7eefb}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1f1f1d;--ink:#ecebe6;--mute:#9b9a94;--line:#33332f;--ok:#5cb27f;--warn:#e08a52;--acc:#7aa5ee;--accbg:#22304a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
header{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;padding:16px 20px}
h1{font-size:20px;margin:0}h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:0 0 10px}
main{display:grid;gap:14px;padding:0 20px 24px;grid-template-columns:minmax(0,2fr) minmax(280px,1fr)}
@media (max-width:860px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
button,select,input,textarea{font:inherit;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);padding:6px 10px}
button{cursor:pointer}button.primary{background:var(--acc);border-color:var(--acc);color:#fff}a{color:var(--acc)}
table{width:100%;border-collapse:collapse}td,th{border-bottom:1px solid var(--line);padding:8px 6px;text-align:left;vertical-align:top}
th{font-size:12px;color:var(--mute);font-weight:600;text-transform:uppercase}
.meal{cursor:pointer;border-radius:8px;padding:6px 8px;min-height:38px}.meal:hover{background:var(--accbg)}
.meal.empty{color:var(--mute)}.mute{color:var(--mute);font-size:13px}
.row{display:flex;justify-content:space-between;gap:8px;padding:5px 0;border-bottom:1px solid var(--line)}.row:last-child{border:0}
.have{color:var(--ok)}.need{font-weight:600}.chips{display:flex;flex-wrap:wrap;gap:6px}
.pill{font-size:13px;padding:2px 8px;border-radius:99px;border:1px solid var(--line)}.x{border:0;background:none;color:var(--mute);padding:0 4px}
textarea{width:100%;min-height:150px}input.full{width:100%}.stack>*+*{margin-top:8px}
dialog{border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink);width:min(520px,94vw)}
dialog::backdrop{background:#0006}
</style>
<header><h1>Meal planner</h1><div style="display:flex;gap:6px;align-items:center">
<button onclick="go(-7)">‹</button><b id="weekLabel"></b><button onclick="go(7)">›</button><a href="/" style="margin-left:10px">Dashboard</a></div></header>
<main>
<div class="stack">
<section class="card"><h2>This week</h2><table id="grid"></table></section>
<section class="card" id="aiCard"><h2>Plan with AI</h2>
<textarea id="req" style="min-height:60px" placeholder="e.g. two vegetarian nights, tacos on Friday, nothing over 40 minutes"></textarea>
<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">
<button class="primary" onclick="plan(false)">Fill empty days</button><button onclick="plan(true)">Replan whole week</button><button onclick="suggest()">What can I make from the pantry?</button></div>
<div id="aiStatus" class="mute" style="margin-top:8px"></div><div id="ideas"></div></section>
</div>
<div class="stack">
<section class="card"><h2>Ingredients this week</h2><div id="ings"></div>
<div class="mute" style="margin-top:8px">Things you don't have are added to the shopping list automatically.</div></section>
<section class="card"><h2>Shopping list</h2><div id="shop" class="chips"></div><div class="mute" style="margin-top:6px">✓ = bought (moves it to the pantry)</div></section>
<section class="card"><h2>Pantry</h2><div id="pantry" class="chips"></div>
<form onsubmit="addPantry(event)" style="display:flex;gap:6px;margin-top:8px"><input id="pantryIn" class="full" placeholder="rice, eggs, olive oil"><button>Add</button></form></section>
<section class="card" id="dietCard"><h2>Diets</h2><div id="diets"></div><div class="mute">Tell the house: "Mia is allergic to peanuts".</div></section>
</div>
</main>
<dialog id="dlg"><form method="dialog" class="stack" onsubmit="saveMeal(event)">
<h2 id="dlgTitle"></h2><input id="mName" class="full" placeholder="Meal name">
<label class="mute">Servings <input id="mServ" type="number" min="1" value="4" style="width:70px"></label>
<textarea id="mIng" placeholder="One ingredient per line, e.g.&#10;1 lb chicken thighs&#10;2 cups rice&#10;3 cloves garlic"></textarea>
<div style="display:flex;gap:6px;justify-content:space-between"><button type="button" onclick="clearMeal()">Clear</button>
<span><button type="button" onclick="dlg.close()">Cancel</button> <button class="primary">Save</button></span></div></form></dialog>
<script>
let D={},week=null,cur=null,A=[];
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const btn=(label,fn,cls='')=>{A.push(fn);return `<button class="${cls}" onclick="A[${A.length-1}]()">${label}</button>`};
const DN={mon:'Monday',tue:'Tuesday',wed:'Wednesday',thu:'Thursday',fri:'Friday',sat:'Saturday',sun:'Sunday'};
async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
 const j=await r.json().catch(()=>({}));if(r.status==401){location.href='/';return D}if(!r.ok){alert(j.error||r.status);return null}return j}
async function load(){const j=await api('/api/meals'+(week?'?week='+week:''));if(j){D=j;week=j.week;draw()}}
function go(days){const d=new Date(week+'T12:00:00');d.setDate(d.getDate()+days);week=d.toISOString().slice(0,10);load()}
function draw(){A=[];weekLabel.textContent='Week of '+new Date(week+'T12:00:00').toLocaleDateString([], {month:'short',day:'numeric'});
 grid.innerHTML='<tr><th></th>'+D.slots.map(s=>`<th>${esc(s)}</th>`).join('')+'</tr>'+D.days.map(d=>`<tr><th>${DN[d]}</th>`+D.slots.map(s=>{const m=(D.plan[d]||{})[s];
  A.push(()=>openMeal(d,s));return `<td><div class="meal ${m?'':'empty'}" onclick="A[${A.length-1}]()">${m?`<b>${esc(m.name)}</b><div class=mute>${m.ingredients.length} ingredients · serves ${m.servings}</div>`:'+ add'}</div></td>`}).join('')+'</tr>').join('');
 ings.innerHTML=D.ingredients.map(i=>`<div class="row"><span class="${i.have?'have':'need'}">${i.have?'✓ ':''}${esc(i.item)}</span><span class=mute>${esc(i.qty)}</span></div>`).join('')||'<span class=mute>Plan some meals to see what you need.</span>';
 shop.innerHTML=D.shopping.map(it=>`<span class=pill>${esc(it)} ${btn('✓',async()=>{await api('/api/list/bought',{item:it});load()},'x')}</span>`).join('')||'<span class=mute>Empty</span>';
 pantry.innerHTML=D.pantry.map(it=>`<span class=pill>${esc(it)} ${btn('✕',async()=>{D=await api('/api/meals/pantry',{week,remove:[it]})||D;draw()},'x')}</span>`).join('')||'<span class=mute>Add what you already have.</span>';
 const ds=Object.entries(D.diets||{});dietCard.style.display=ds.length?'':'none';
 diets.innerHTML=ds.map(([p,d])=>`<div class="row"><span>${esc(p)}</span><span class=mute>${Object.entries(d).filter(([k,v])=>v&&v.length).map(([k,v])=>esc(k)+': '+esc([].concat(v).join(', '))).join(' · ')}</span></div>`).join('');
 aiCard.style.display=D.ai?'':'none'}
function openMeal(d,s){cur={d,s};const m=(D.plan[d]||{})[s];dlgTitle.textContent=DN[d]+' '+s;mName.value=m?m.name:'';mServ.value=m?m.servings:4;
 mIng.value=m?m.ingredients.map(i=>(i.qty?i.qty+' ':'')+i.item).join('\n'):'';dlg.showModal();mName.focus()}
async function saveMeal(e){e.preventDefault();const name=mName.value.trim();
 D=await api('/api/meals/set',{week,day:cur.d,slot:cur.s,meal:name?{name,servings:+mServ.value||4,ingredients:mIng.value}:null})||D;dlg.close();draw()}
async function clearMeal(){D=await api('/api/meals/set',{week,day:cur.d,slot:cur.s,meal:null})||D;dlg.close();draw()}
async function addPantry(e){e.preventDefault();const items=pantryIn.value.split(',').map(x=>x.trim()).filter(Boolean);if(!items.length)return;
 D=await api('/api/meals/pantry',{week,add:items})||D;pantryIn.value='';draw()}
async function plan(replace){if(replace&&!confirm('Replace every meal this week?'))return;aiStatus.textContent='Planning… this takes a few seconds.';
 const j=await api('/api/meals/plan',{week,request:req.value,replace});aiStatus.textContent=j?'Done. The shopping list is updated.':'';if(j){D=j;draw()}}
async function suggest(){aiStatus.textContent='Looking through the pantry…';const j=await api('/api/meals/suggest',{request:req.value});aiStatus.textContent='';if(!j)return;A=[];
 ideas.innerHTML=j.ideas.map(i=>`<div class="row"><span><b>${esc(i.name)}</b> <span class=mute>${i.minutes} min · uses ${esc(i.uses.join(', '))}${i.missing.length?' · need '+esc(i.missing.join(', ')):''}</span></span></div>`).join('')}
load();
</script></html>"""
