"""The Voice page (served at /voice): the voice modeler. Pick an accent, turn moods on, tune each
mood's knobs with sliders, and hear a line in any mood on this phone or on the house's speakers."""

VOICE_PAGE = r"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Voice</title>
<style>
:root{--bg:#f4f4f1;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#e2e2dc;--ok:#2f7d4f;--warn:#b3541e;--acc:#2a5db0}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1f1f1d;--ink:#ecebe6;--mute:#9b9a94;--line:#33332f;--ok:#5cb27f;--warn:#e08a52;--acc:#7aa5ee}}
*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
header{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;padding:16px 20px}
h1{font-size:20px;margin:0}h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:0 0 10px}
main{display:grid;gap:14px;padding:0 20px 24px;grid-template-columns:minmax(0,1.4fr) minmax(280px,1fr)}
@media (max-width:860px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
button,select,input,textarea{font:inherit;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);padding:5px 9px}
button{cursor:pointer}a{color:var(--acc)}.mute{color:var(--mute);font-size:13px}
textarea{width:100%;min-height:64px;resize:vertical}
.moods{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}.moods button[aria-pressed=true]{background:var(--acc);color:#fff;border-color:var(--acc)}
.knob{display:grid;grid-template-columns:110px 1fr 56px;gap:10px;align-items:center;padding:6px 0}
.knob output{text-align:right;font-variant-numeric:tabular-nums}.knob .t{color:var(--warn)}
input[type=range]{width:100%;padding:0}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px}
label.sw{display:flex;gap:8px;align-items:center}
</style>
<header><h1>Voice</h1><a href="/">Dashboard</a></header>
<main>
<section class="card"><h2>Moods</h2>
<div class="moods" id="moods"></div>
<div id="about" class="mute" style="margin-bottom:8px"></div>
<div id="knobs"></div>
<div class="row"><button id="save">Save this mood</button><button id="reset">Back to built-in</button>
<span id="saved" class="mute"></span></div>
</section>
<div style="display:grid;gap:14px;align-content:start">
<section class="card"><h2>Try it</h2>
<textarea id="text">Righto, the kitchen lights are off. Try not to trip over the dog on the way out.</textarea>
<div class="row"><button id="here">Play here</button><button id="house">Say it in the house</button>
<select id="room"><option value="">hub speaker</option></select></div>
<audio id="player" controls hidden style="width:100%;margin-top:10px"></audio>
<div class="mute" style="margin-top:6px">Plays the selected mood with the sliders as they are now, before you save.</div>
</section>
<section class="card"><h2>Accent and moods</h2>
<label class="sw"><input type="checkbox" id="enabled"> Change the delivery for each line</label>
<div class="mute" style="margin:4px 0 10px">Safety lines (smoke, CO, leaks, the alarm) are always said slow and clear.</div>
<label class="sw">Accent <select id="accent"></select></label>
<div class="mute" id="engine" style="margin-top:8px"></div>
</section>
</div></main>
<script>
let D=null,cur='cheeky',edit={};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
 const j=await r.json().catch(()=>({}));if(r.status==401){location.href='/';return null}if(!r.ok){alert(j.error||r.status);return null}return j}
const LABEL={rate:'Speed',pitch:'Pitch (semitones)',pause:'Pauses',lively:'Liveliness',volume:'Loudness (dB)',exaggeration:'Acting (neural)'};
async function load(){const j=await api('/api/voice/tune');if(!j)return;D=j;
 if(!D.emotions[cur])cur=Object.keys(D.emotions)[0];edit={...D.emotions[cur].knobs};draw()}
function draw(){
 moods.innerHTML=Object.keys(D.emotions).map(m=>`<button data-m="${esc(m)}" aria-pressed="${m==cur}">${esc(m)}${D.emotions[m].tuned.length?' *':''}</button>`).join('');
 about.textContent=D.emotions[cur].about;
 knobs.innerHTML=Object.entries(D.knobs).map(([k,r])=>{const st=(r.max-r.min)/100;
  return `<div class="knob"><span class="${D.emotions[cur].tuned.includes(k)?'t':''}">${esc(LABEL[k]||k)}</span>
  <input type="range" data-k="${k}" min="${r.min}" max="${r.max}" step="${st.toFixed(3)}" value="${edit[k]}">
  <output id="o_${k}">${(+edit[k]).toFixed(2)}</output></div>`}).join('');
 enabled.checked=D.enabled;
 accent.innerHTML='<option value="">none (the voice\'s own)</option>'+Object.entries(D.accents).map(([k,n])=>`<option value="${k}"${D.accent==k?' selected':''}>${esc(n)} (${k})</option>`).join('');
 engine.textContent=`Engine: ${D.engine}${D.engine_note?' — '+D.engine_note:''}`;
 const rs=D.rooms||[];if(room.options.length<=1)rs.forEach(r=>room.add(new Option(r,r)))}
moods.addEventListener('click',e=>{const m=e.target.dataset.m;if(!m)return;cur=m;edit={...D.emotions[m].knobs};saved.textContent='';draw()});
knobs.addEventListener('input',e=>{const k=e.target.dataset.k;if(!k)return;edit[k]=+e.target.value;document.getElementById('o_'+k).textContent=edit[k].toFixed(2)});
save.onclick=async()=>{const r=await api('/api/voice/tune/save',{emotion:cur,knobs:edit});if(r){saved.textContent='Saved.';load()}};
reset.onclick=async()=>{if(await api('/api/voice/tune/reset',{emotion:cur})){saved.textContent='Back to built-in.';load()}};
enabled.onchange=()=>api('/api/voice/tune/options',{enabled:enabled.checked}).then(load);
accent.onchange=()=>api('/api/voice/tune/options',{accent:accent.value||null}).then(load);
here.onclick=async()=>{here.disabled=true;try{
 const r=await fetch('/api/voice/tune/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:text.value,emotion:cur,knobs:edit})});
 if(!r.ok){const j=await r.json().catch(()=>({}));alert(j.error||r.status);return}
 player.src=URL.createObjectURL(await r.blob());player.hidden=false;player.play()}finally{here.disabled=false}};
house.onclick=()=>api('/api/voice/tune/say',{text:text.value,emotion:cur,knobs:edit,room:room.value||null});
load();
</script>
"""
