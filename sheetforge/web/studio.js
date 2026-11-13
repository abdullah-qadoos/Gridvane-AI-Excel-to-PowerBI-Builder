"use strict";
// Workspace tabs, the Excel formula fixer, and the Power BI designer canvas.
// Shares app.js globals ($, el, json, task, run, project, cleaned, controls, renderModel, ...).
const PAGE_W=1280,PAGE_H=720,HEADER_H=64,SNAP=8,MIN_BOX=40,MAX_VISUALS=30;
const NOFILL=new Set(['pie','donut','treemap','map','filled_map','shape_map','azure_map','table','matrix','slicer','button_slicer','list_slicer','text_slicer']);
const TEXTBOXES=new Set(['text','metrics','paginated','image','arcgis','power_apps','power_automate']);
// Extra colors beyond the theme palette, offered as one-click swatches.
const EXTRA_COLORS=['#FFFFFF','#F3ECDD','#15130F','#3E3A32','#B5562A','#E4572E','#F3A712','#E9C46A','#2F5A43','#0F9D6B','#2BA8A0','#1F6FB2','#58A6FF','#6C5CE7','#C2416B','#F778BA'];
let pictures={},catalog=null,visualInfo={},designPage=0,selected=null,drag=null,formulaScan=null,formulaChoices={};
const reduced=()=>matchMedia('(prefers-reduced-motion: reduce)').matches;
const safeColor=(value,fallback)=>/^#[0-9a-f]{6}$/i.test(value||'')?value:fallback;
const snap=value=>Math.round(value/SNAP)*SNAP,clamp=(value,low,high)=>Math.max(low,Math.min(high,value));

/* ---------- tabs ---------- */
function showTab(name){
  for(const button of document.querySelectorAll('.page-tabs [role=tab]')){const on=button.dataset.tab===name;button.setAttribute('aria-selected',String(on));button.tabIndex=on?0:-1;}
  for(const page of document.querySelectorAll('[data-page]'))page.hidden=page.dataset.page!==name;
  preference('tab',name);
  if(name==='formulas'&&project&&!formulaScan&&!busy)task('Scanning your formulas…',scanFormulas);
  if(name==='design')renderDesign();
}
for(const button of document.querySelectorAll('.page-tabs [role=tab]')){
  button.addEventListener('click',()=>showTab(button.dataset.tab));
  button.addEventListener('keydown',event=>{const tabs=[...document.querySelectorAll('.page-tabs [role=tab]')],step={ArrowRight:1,ArrowLeft:-1}[event.key];if(!step)return;event.preventDefault();const next=tabs[(tabs.indexOf(button)+step+tabs.length)%tabs.length];showTab(next.dataset.tab);next.focus();});
}
for(const button of document.querySelectorAll('[data-go]'))button.addEventListener('click',()=>{showTab(button.dataset.go);$('workspace').scrollIntoView({behavior:reduced()?'instant':'smooth',block:'start'});});

/* ---------- formulas ---------- */
const FKIND={typed_total:['Totals typed as numbers','A typed total goes stale when a number changes. A formula keeps it right.'],broken:['Broken formulas','Cells that show an error in Excel.'],hardcoded:['Numbers inside formulas','The same number typed into formulas. One named input cell means you change it once.']};
function resetFormulas(){formulaScan=null;formulaChoices={};renderFormulas();}
async function scanFormulas(){
  const result=await json('/formulas/scan',{project_id:project.id,privacy:privacy()});
  formulaScan=result;formulaChoices={};
  for(const fix of result.fixes)formulaChoices[fix.id]={on:fix.ready,name:fix.name||''};
  renderFormulas();
  return result.fixes.length?'':'No formula problems found.';
}
function renderFormulas(){
  const target=$('formula-list'),badge=$('formula-badge');target.replaceChildren();
  if(!formulaScan){target.append(el('p',project?'Select Scan again to check this workbook’s formulas.':'Open a workbook to scan its formulas.','helper'));badge.hidden=true;$('formula-notes').textContent='';studioControls();return;}
  const fixes=formulaScan.fixes;
  badge.hidden=false;badge.textContent=fixes.length?fixes.length+(fixes.length===1?' fix found':' fixes found'):'✓ Healthy';badge.className=fixes.length?'layout-chip changed':'layout-chip';
  const stats=el('div',null,'stats');stats.append(el('span',formulaScan.formulas.toLocaleString()+' formulas checked'),el('span',fixes.filter(f=>f.ready).length+' ready to fix'));target.append(stats);
  for(const [kind,[title,hint]] of Object.entries(FKIND)){
    const items=fixes.filter(fix=>fix.kind===kind);if(!items.length)continue;
    const group=el('section',null,'formula-group');group.append(el('h3',title+' · '+items.length),el('p',hint,'helper'));const cards=el('div',null,'formula-cards');group.append(cards);
    for(const fix of items){
      const choice=formulaChoices[fix.id],card=el('article',null,'formula-card'+(fix.ready?'':' stuck')),head=el('label',null,'formula-head'),box=el('input'),text=el('div');
      box.type='checkbox';box.checked=choice.on&&fix.ready;box.disabled=!fix.ready;box.addEventListener('change',()=>{choice.on=box.checked;studioControls();});
      text.append(el('strong',fix.title),el('p',fix.detail,'helper'));head.append(box,text);card.append(head);
      if(kind==='hardcoded'){const row=el('label',null,'formula-name'),input=el('input');input.value=choice.name;input.spellcheck=false;input.setAttribute('aria-label','Name for the input '+fix.value);input.addEventListener('input',()=>{choice.name=input.value.trim();});row.append(el('span','Name it'),input);card.append(row);}
      const list=el('ul',null,'formula-changes');
      for(const change of fix.changes.slice(0,3)){const item=el('li');item.append(el('span',change.cell,'cell'),el('code',change.old||'(empty)','old'),el('span','→','arrow'),el('code',change.new,'new'));list.append(item);}
      if(fix.changes.length>3)list.append(el('li','+ '+(fix.changes.length-3)+' more cells','helper'));
      if(fix.ready)card.append(list);
      cards.append(card);
    }
    target.append(group);
  }
  if(!fixes.length)target.append(el('p','✓ Nothing to fix. Your formulas look healthy.','model-empty'));
  $('formula-notes').textContent=(formulaScan.notes||[]).join(' ');
  studioControls();
}
async function applyFormulas(){
  const accepted={};
  for(const fix of formulaScan.fixes){const choice=formulaChoices[fix.id];if(choice.on&&fix.ready)accepted[fix.id]=fix.kind==='hardcoded'&&choice.name&&choice.name!==fix.name?choice.name:null;}
  const result=await json('/formulas/apply',{project_id:project.id,accepted});
  await download(result.download,'formulas-fixed.xlsx');
  return 'Saved a fixed copy with '+result.applied+(result.applied===1?' fix':' fixes')+'. Your original file is unchanged.';
}
$('scan-formulas').addEventListener('click',()=>task('Scanning your formulas…',scanFormulas));
$('apply-formulas').addEventListener('click',()=>task('Saving the fixed copy…',applyFormulas));

/* ---------- designer state ---------- */
function design(){try{const value=JSON.parse($('model-json').value);return value&&Array.isArray(value.pages)?value:null;}catch{return null;}}
function designDirty(){const saved=run?.model_plan_json;if(!saved)return false;try{return pretty(JSON.parse(saved))!==$('model-json').value;}catch{return true;}}
const history={past:[],future:[]};
function snapshot(){history.past.push($('model-json').value);if(history.past.length>50)history.past.shift();history.future=[];}
function restore(from,to){if(!from.length||busy)return;to.push($('model-json').value);$('model-json').value=from.pop();selected=null;renderModel();controls();}
document.addEventListener('keydown',event=>{if(!(event.ctrlKey||event.metaKey)||!design())return;if(event.target instanceof Element&&event.target.closest('input,textarea,select'))return;const key=event.key.toLowerCase();if(key==='z'&&!event.shiftKey)restore(history.past,history.future);else if(key==='y'||(key==='z'&&event.shiftKey))restore(history.future,history.past);else return;event.preventDefault();});
function editDesign(change){const value=design();if(!value)return;snapshot();change(value);$('model-json').value=pretty(value);renderModel();controls();}
function currentVisuals(){return design()?.pages[designPage]?.visuals||[];}
async function saveDesign(){
  const value=design();if(!value)throw new Error('Build a project first.');
  if(!designDirty()&&run.status==='generated')return;
  const modeled=await json('/model',{...ids(),model_plan:value,privacy:privacy()});run=modeled.run;$('model-json').value=pretty(modeled.model_plan);
  run=(await json('/generate',ids())).run;renderModel();await refreshRuns();
}
function studioControls(){
  if(!$('designer'))return;
  $('scan-formulas').disabled=busy||!project;
  for(const box of document.querySelectorAll('.formula-card.stuck input'))box.disabled=true;
  $('apply-formulas').disabled=busy||!formulaScan||!formulaScan.fixes.some(fix=>fix.ready&&formulaChoices[fix.id]?.on);
  const ready=!!design()&&!!run&&CHECKED_STATES.includes(run.status),dirty=designDirty();
  $('design-save').disabled=busy||!ready||(!dirty&&run.status==='generated');
  $('design-arrange').disabled=busy||!ready;$('design-ai').disabled=busy||!ready;$('add-visual').disabled=busy||!ready||currentVisuals().length>=MAX_VISUALS;
  const badge=$('design-badge');badge.hidden=!ready;badge.textContent=dirty?'Unsaved changes':run?.status==='generated'?'✓ Saved':'Not built yet';badge.className=dirty||run?.status!=='generated'?'layout-chip changed':'layout-chip';
}
async function loadCatalog(){if(catalog)return;catalog=await json('/visuals');visualInfo=Object.fromEntries(catalog.visuals.map(item=>[item.key,item]));$('design-theme').replaceChildren(...Object.entries(catalog.themes).map(([key,theme])=>new Option(theme.label,key)));renderDesign();}

/* ---------- icons (simplified Power BI visual glyphs) ---------- */
function iconSvg(key,color){
  const r=(x,y,w,h,o=1)=>`<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="1" fill="${color}" opacity="${o}"/>`;
  const line=(d,o=1)=>`<path d="${d}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" opacity="${o}"/>`;
  const fill=(d,o=1)=>`<path d="${d}" fill="${color}" opacity="${o}"/>`;
  const dot=(x,y,s=1.8,o=1)=>`<circle cx="${x}" cy="${y}" r="${s}" fill="${color}" opacity="${o}"/>`;
  const text=(value,size)=>`<text x="12" y="${12+size*.36}" text-anchor="middle" font-size="${size}" font-weight="700" fill="${color}" font-family="Segoe UI,Inter,sans-serif">${value}</text>`;
  const shapes={
    bar_stacked:r(3,4,8,4)+r(11,4,7,4,.45)+r(3,10,11,4)+r(14,10,6,4,.45)+r(3,16,6,4)+r(9,16,5,4,.45),
    column_stacked:r(4,12,4,8)+r(4,7,4,5,.45)+r(10,9,4,11)+r(10,4,4,5,.45)+r(16,14,4,6)+r(16,9,4,5,.45),
    bar:r(3,4,14,4)+r(3,10,18,4,.55)+r(3,16,10,4),
    column:r(4,10,4,10)+r(10,5,4,15,.55)+r(16,8,4,12),
    bar_100:r(3,4,12,4)+r(15,4,6,4,.45)+r(3,10,8,4)+r(11,10,10,4,.45)+r(3,16,15,4)+r(18,16,3,4,.45),
    column_100:r(4,10,4,10)+r(4,4,4,6,.45)+r(10,7,4,13)+r(10,4,4,3,.45)+r(16,13,4,7)+r(16,4,4,9,.45),
    line:line('M3 17 L9 10 L14 13 L21 5'),
    area:fill('M3 20 L3 15 L9 9 L14 12 L21 5 L21 20 Z',.45)+line('M3 15 L9 9 L14 12 L21 5'),
    area_stacked:fill('M3 20 L3 12 L9 8 L14 10 L21 4 L21 20 Z',.35)+fill('M3 20 L3 16 L9 13 L14 15 L21 11 L21 20 Z',.85),
    combo_stacked:r(4,12,4,8,.55)+r(4,9,4,3,.3)+r(10,9,4,11,.55)+r(10,6,4,3,.3)+r(16,11,4,9,.55)+line('M4 6 L12 3 L20 6'),
    combo:r(4,12,4,8,.55)+r(10,9,4,11,.55)+r(16,11,4,9,.55)+line('M4 8 L12 4 L20 7'),
    ribbon:fill('M3 6 L9 6 L15 12 L21 12 L21 16 L15 16 L9 10 L3 10 Z',.5)+fill('M3 14 L9 14 L15 6 L21 6 L21 9 L15 9 L9 18 L3 18 Z',.9),
    waterfall:r(3,12,4,8)+r(8,7,4,5,.55)+r(13,9,4,3,.55)+r(18,4,3,16),
    funnel:r(3,4,18,4)+r(6,10,12,4,.7)+r(9,16,6,4,.45),
    scatter:dot(6,15)+dot(9,9)+dot(13,13)+dot(16,6)+dot(19,11)+dot(11,18),
    pie:dot(12,12,8,.4)+fill('M12 12 L12 4 A8 8 0 0 1 19.6 14.5 Z'),
    donut:`<circle cx="12" cy="12" r="7" fill="none" stroke="${color}" stroke-width="4" opacity=".4"/><path d="M12 5 A7 7 0 0 1 18.6 14.4" fill="none" stroke="${color}" stroke-width="4"/>`,
    treemap:r(3,4,10,16)+r(14,4,7,9,.6)+r(14,14,7,6,.35),
    map:fill('M3 6 L9 4 L15 6 L21 4 L21 18 L15 20 L9 18 L3 20 Z',.3)+dot(10,11,2.6)+dot(16,14,1.6),
    filled_map:fill('M4 8 L10 5 L14 8 L20 6 L19 14 L14 19 L8 17 L4 13 Z',.85),
    shape_map:fill('M5 6 L11 4 L13 9 L19 8 L20 15 L13 20 L6 16 Z',.55)+line('M11 4 L13 9 L8 13 L6 16'),
    azure_map:fill('M12 3 L19 21 L12 17 L5 21 Z',.9),
    gauge:line('M4 17 A8 8 0 0 1 20 17',.35)+line('M4 17 A8 8 0 0 1 14.5 9.4')+line('M12 17 L15.5 11'),
    card:text('123',9),
    multi_card:r(3,4,18,7,.3)+r(3,13,18,7,.3)+r(5,6.5,8,2)+r(5,15.5,6,2),
    kpi:line('M4 18 L9 12 L13 15 L20 6')+line('M15.5 6 L20 6 L20 10.5'),
    slicer:fill('M4 5 L20 5 L14 12 L14 19 L10 17 L10 12 Z',.85),
    button_slicer:r(3,5,8,6)+r(13,5,8,6,.45)+r(3,13,8,6,.45)+r(13,13,8,6,.45),
    list_slicer:r(3,5,3,3)+r(8,5,13,3,.5)+r(3,11,3,3)+r(8,11,13,3,.5)+r(3,17,3,3,.4)+r(8,17,13,3,.3),
    text_slicer:`<circle cx="10" cy="10" r="5" fill="none" stroke="${color}" stroke-width="2"/>`+line('M14 14 L19 19'),
    table:r(3,4,18,4)+r(3,10,18,3,.35)+r(3,15,18,3,.35),
    matrix:r(3,4,18,4)+r(3,10,5,10,.6)+r(9,10,12,4,.3)+r(9,16,12,4,.3),
    r_script:text('R',13),python:text('Py',10),
    key_influencers:line('M5 19 L5 5')+r(7,5,8,3)+r(7,10.5,12,3,.6)+r(7,16,5,3,.35),
    decomposition:dot(5,12,2)+line('M7 12 L11 6 M7 12 L11 12 M7 12 L11 18')+r(12,4,8,4,.85)+r(12,10,6,4,.55)+r(12,16,4,4,.35),
    qna:text('Q&amp;A',7.5),
    narrative:r(3,5,18,2)+r(3,9,14,2,.6)+r(3,13,18,2,.6)+r(3,17,10,2,.4),
    text:text('T',14),
    metrics:fill('M8 4 L16 4 L16 9 A4 4 0 0 1 8 9 Z')+line('M12 14 L12 18 M8 20 L16 20'),
    paginated:fill('M6 3 L15 3 L19 7 L19 21 L6 21 Z',.3)+r(8.5,12,2.5,6)+r(12,9,2.5,9,.7)+r(15.5,14,2,4,.5),
    image:r(3,5,18,14,.22)+line('M5 17 L10 11 L14 15 L16 13 L20 17')+dot(16,9,1.6),
    arcgis:`<circle cx="12" cy="12" r="8" fill="none" stroke="${color}" stroke-width="2"/>`+line('M4 12 L20 12 M12 4 C8 8 8 16 12 20 C16 16 16 8 12 4'),
    power_apps:fill('M12 3 L20 12 L12 21 L4 12 Z',.9),
    power_automate:fill('M3 5 L13 5 L20 12 L13 19 L3 19 L10 12 Z',.9),
  };
  return `<svg viewBox="0 0 24 24" width="100%" height="100%" aria-hidden="true" focusable="false">${shapes[key]||text('?',12)}</svg>`;
}

/* ---------- canvas ---------- */
const percent=(value,total)=>(value/total*100)+'%';
function place(node,box){node.style.left=percent(box.x,PAGE_W);node.style.top=percent(box.y,PAGE_H);node.style.width=percent(box.w,PAGE_W);node.style.height=percent(box.h,PAGE_H);}
function isDark(hex){const [r,g,b]=[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16));return .299*r+.587*g+.114*b<128;}
function themeOf(value){return catalog.themes[value.theme||'pivotra']||Object.values(catalog.themes)[0];}
function focusBox(index){document.querySelectorAll('#canvas .vbox')[index]?.focus();}
function renderDesign(){
  if(!$('designer'))return;
  if(!catalog){if(project)loadCatalog().catch(error=>notice(error.message,true));return;}
  const value=design(),canvas=$('canvas'),pages=$('design-pages'),swatch=$('theme-swatch');
  canvas.replaceChildren();pages.replaceChildren();$('inspector').replaceChildren();swatch.replaceChildren();canvas.style.background='';canvas.style.backgroundImage='';
  if(!value){canvas.append(el('p',run?.status==='needs_fix'?'Paused at the data quality check. Fix your file and upload it again.':'Build a project, and your report pages appear here to design.','helper canvas-empty'));$('inspector').append(el('p','Nothing to edit yet.','helper'));studioControls();return;}
  designPage=Math.min(designPage,value.pages.length-1);
  const page=value.pages[designPage],theme=themeOf(value);
  if(selected!=null&&!page.visuals[selected])selected=null;
  $('design-theme').value=value.theme||'pivotra';
  for(const color of theme.colors){const chip=el('i');chip.style.background=color;swatch.append(chip);}
  value.pages.forEach((item,index)=>{const button=el('button',item.title,'page-chip'+(index===designPage?' active':''));button.type='button';button.setAttribute('aria-pressed',String(index===designPage));button.addEventListener('click',()=>{designPage=index;selected=null;renderDesign();});pages.append(button);});
  const add=el('button','+ Page','page-chip add');add.type='button';add.disabled=busy||value.pages.length>=33;add.addEventListener('click',addPage);pages.append(add);
  canvas.style.background=safeColor(page.background,theme.page);
  if(page.image){if(pictures[page.image]){canvas.style.backgroundImage='url("'+pictures[page.image]+'")';canvas.style.backgroundSize={Fit:'100% 100%',Fill:'cover',Normal:'auto'}[page.image_fit||'Fit'];canvas.style.backgroundRepeat='no-repeat';canvas.style.backgroundPosition=page.image_fit==='Normal'?'left top':'center';}else loadPicture(page.image);}
  const band=el('div',(project?.name||'Report').replace(/\.[^.]+$/,'')+'  ·  '+page.title,'canvas-band');const [bandBack,bandText]=isDark(theme.page)?[theme.card,theme.ink]:[theme.ink,theme.page];band.style.background=bandBack;band.style.color=bandText;canvas.append(band);
  page.visuals.forEach((visual,index)=>canvas.append(boxFor(visual,index,theme,page.transparency||0)));
  renderInspector(value,page,theme);
  studioControls();
}
function seeThrough(hex,percent){const [r,g,b]=[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16));return `rgba(${r},${g},${b},${1-percent/100})`;}
async function loadPicture(name){if(pictures[name]!==undefined)return;pictures[name]='';try{const blob=await(await api('/design/image?project_id='+project.id+'&name='+encodeURIComponent(name))).blob();pictures[name]=await new Promise((done,fail)=>{const reader=new FileReader();reader.onload=()=>done(reader.result);reader.onerror=fail;reader.readAsDataURL(blob);});renderDesign();}catch(error){delete pictures[name];notice(error.message,true);}}
function boxFor(visual,index,theme,transparency=0){
  const info=visualInfo[visual.kind]||{label:visual.kind},box=el('div',null,'vbox'+(selected===index?' selected':''));
  box.tabIndex=0;box.setAttribute('role','button');box.setAttribute('aria-label',info.label+': '+visual.title+'. Enter to edit, arrow keys to move, Shift and arrows to resize.');
  place(box,visual);box.style.background=seeThrough(safeColor(visual.background,theme.card),transparency);box.style.borderColor=theme.line;box.style.color=theme.ink;
  const art=el('span',null,'vbox-art');art.innerHTML=iconSvg(visual.kind,safeColor(visual.color,theme.colors[0]));
  const handle=el('span',null,'vbox-handle');handle.setAttribute('aria-hidden','true');
  box.append(el('span',visual.title,'vbox-title'),art,el('span',info.label,'vbox-kind'),handle);
  box.addEventListener('pointerdown',event=>startDrag(event,index,event.target===handle?'resize':'move'));
  box.addEventListener('keydown',event=>nudge(event,index));
  return box;
}
function startDrag(event,index,mode){
  if(busy||event.button!==0)return;
  const visual=currentVisuals()[index],box=event.currentTarget;if(!visual)return;
  event.preventDefault();box.setPointerCapture(event.pointerId);box.focus({preventScroll:true});
  const start={x:visual.x,y:visual.y,w:visual.w,h:visual.h};
  drag={index,mode,box,sx:event.clientX,sy:event.clientY,start,now:start,scale:PAGE_W/$('canvas').clientWidth,moved:false};
  box.addEventListener('pointermove',moveDrag);box.addEventListener('pointerup',endDrag,{once:true});box.addEventListener('pointercancel',endDrag,{once:true});
}
function moveDrag(event){
  if(!drag)return;
  const dx=(event.clientX-drag.sx)*drag.scale,dy=(event.clientY-drag.sy)*drag.scale,start=drag.start;
  if(Math.abs(dx)+Math.abs(dy)>3)drag.moved=true;
  drag.now=drag.mode==='move'?{...start,x:clamp(snap(start.x+dx),0,PAGE_W-start.w),y:clamp(snap(start.y+dy),HEADER_H,PAGE_H-start.h)}:{...start,w:clamp(snap(start.w+dx),MIN_BOX,PAGE_W-start.x),h:clamp(snap(start.h+dy),MIN_BOX,PAGE_H-start.y)};
  place(drag.box,drag.now);
}
function endDrag(){
  if(!drag)return;
  const {index,box,now,moved}=drag;box.removeEventListener('pointermove',moveDrag);drag=null;selected=index;
  if(moved)editDesign(value=>Object.assign(value.pages[designPage].visuals[index],now));else renderDesign();
  focusBox(index);
}
function nudge(event,index){
  if(busy)return;
  if(event.key==='Enter'||event.key===' '){event.preventDefault();selected=index;renderDesign();focusBox(index);return;}
  if(event.key==='Delete'||event.key==='Backspace'){event.preventDefault();removeVisual(index);return;}
  const step={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[event.key];if(!step)return;event.preventDefault();
  selected=index;
  editDesign(value=>{const visual=value.pages[designPage].visuals[index];
    if(event.shiftKey){visual.w=clamp(visual.w+step[0]*SNAP,MIN_BOX,PAGE_W-visual.x);visual.h=clamp(visual.h+step[1]*SNAP,MIN_BOX,PAGE_H-visual.y);}
    else{visual.x=clamp(visual.x+step[0]*SNAP,0,PAGE_W-visual.w);visual.y=clamp(visual.y+step[1]*SNAP,HEADER_H,PAGE_H-visual.h);}});
  focusBox(index);
}

/* ---------- editing ---------- */
// A good "group by" is a short text column, not a date or a near-unique ID.
function guessCategory(table){const data=cleaned[table];if(!data)return null;const text=column=>data.rows.some(row=>typeof row[column]==='string'&&!/^\d{4}-\d{2}-\d{2}/.test(row[column]));const few=column=>new Set(data.rows.map(row=>row[column])).size<Math.max(3,data.rows.length*.8);return data.columns.find(column=>text(column)&&few(column)&&!/(^|[ _-])(id|code|key)$/i.test(column))||data.columns.find(text)||null;}
function guessMeasure(value,table){const own=value.measures.filter(item=>item.table===table);return (own.find(item=>!/^COUNTROWS/i.test(item.dax))||own[0]||value.measures[0]).name;}
function overlaps(a,b){return a.x<b.x+b.w&&b.x<a.x+a.w&&a.y<b.y+b.h&&b.y<a.y+a.h;}
function freeSpot(visuals,w,h){
  for(const [width,height] of [[w,h],[w/2,h/2]])
    for(let y=HEADER_H+16;y+height<=PAGE_H;y+=SNAP)for(let x=24;x+width<=PAGE_W;x+=SNAP){const box={x,y,w:width,h:height};if(!visuals.some(other=>overlaps(box,other)))return box;}
  return {x:24,y:HEADER_H+16,w:240,h:136};
}
function addVisual(){
  const value=design();if(!value)return;
  const table=value.tables[0].name,category=guessCategory(table),measure=guessMeasure(value,table);
  const spot=freeSpot(currentVisuals(),384,216);
  editDesign(next=>next.pages[designPage].visuals.push(category?{kind:'column',table,title:measure+' by '+category,measure,category,columns:[],...spot}:{kind:'card',table,title:measure,measure,columns:[],...spot}));
  selected=currentVisuals().length-1;renderDesign();focusBox(selected);
}
function removeVisual(index){
  if(currentVisuals().length<=1){notice('A page needs at least one visual. Delete the page instead.',true);return;}
  editDesign(value=>value.pages[designPage].visuals.splice(index,1));selected=null;renderDesign();
}
function addPage(){
  editDesign(value=>{let n=value.pages.length+1;while(value.pages.some(page=>page.name.toLowerCase()==='page '+n))n++;const measure=value.measures[0];
    value.pages.push({name:'Page '+n,title:'Page '+n,visuals:[{kind:'card',table:measure.table,title:measure.name,measure:measure.name,columns:[],x:24,y:88,w:296,h:120}]});});
  designPage=design().pages.length-1;selected=null;renderDesign();
}
function field(target,label,control){const wrap=el('label',null,'field');wrap.append(el('span',label),control);target.append(wrap);return control;}
function colorField(label,value,fallback,change,palette=[]){
  const wrap=el('div',null,'color-field'),input=el('input'),reset=el('button','Use theme','text-button'),swatches=el('div',null,'swatches');
  for(const color of [...new Set([...palette,...EXTRA_COLORS])]){const chip=el('button',null,'swatch'+(value&&value.toUpperCase()===color.toUpperCase()?' active':''));chip.type='button';chip.style.background=color;chip.title=color;chip.setAttribute('aria-label',label+' '+color);chip.addEventListener('click',()=>change(color.toUpperCase()));swatches.append(chip);}
  input.type='color';input.value=safeColor(value,fallback);input.setAttribute('aria-label',label);input.addEventListener('change',()=>change(input.value.toUpperCase()));
  reset.type='button';reset.disabled=!value;reset.addEventListener('click',()=>change(null));
  wrap.append(el('span',label,'field-label'),input,reset,swatches);return wrap;
}
function renderInspector(value,page,theme){
  const target=$('inspector');
  if(selected==null){
    target.append(el('h3','This page'));
    const title=el('input');title.value=page.title;title.maxLength=60;title.addEventListener('change',()=>editDesign(next=>{next.pages[designPage].title=title.value.trim()||'Page';}));field(target,'Page name',title);
    const updatePage=change=>editDesign(next=>change(next.pages[designPage]));
    target.append(colorField('Page background',page.background,theme.page,color=>updatePage(next=>{next.background=color;}),[theme.page,theme.card,...theme.colors]));
    const picture=el('div',null,'picture-field');picture.append(el('span','Background picture','field-label'));
    const choosePicture=el('button',page.image?'Change picture':'Add picture','secondary');choosePicture.type='button';choosePicture.addEventListener('click',()=>$('picture-input').click());picture.append(choosePicture);
    if(page.image){const remove=el('button','Remove','text-button');remove.type='button';remove.addEventListener('click',()=>updatePage(next=>{next.image=null;}));picture.append(remove);
      field(picture,'Picture size',choose([['Fit','Stretch to the page'],['Fill','Fill and crop'],['Normal','Original size']],page.image_fit||'Fit',fit=>updatePage(next=>{next.image_fit=fit;}),'Picture size'));}
    picture.append(el('p','PNG or JPG, up to 5 MB. 1280 \u00d7 720 fits best.','helper'));target.append(picture);
    const clear=el('input');clear.type='range';clear.min='0';clear.max='90';clear.step='10';clear.value=String(page.transparency||0);
    const clearLabel=el('span','See-through boxes: '+(page.transparency||0)+'%');clear.addEventListener('input',()=>{clearLabel.textContent='See-through boxes: '+clear.value+'%';});clear.addEventListener('change',()=>updatePage(next=>{next.transparency=Number(clear.value);}));
    const clearWrap=el('label',null,'field');clearWrap.append(clearLabel,clear);target.append(clearWrap);
    target.append(el('p','Click a box on the page to change its chart, data and colors. Or add a new visual.','helper'));
    if(value.pages.length>1){const remove=el('button','Delete this page','secondary danger');remove.type='button';remove.addEventListener('click',()=>{editDesign(next=>next.pages.splice(designPage,1));designPage=Math.max(0,designPage-1);renderDesign();});target.append(remove);}
    return;
  }
  const visual=page.visuals[selected],info=visualInfo[visual.kind];
  const update=change=>{editDesign(next=>change(next.pages[designPage].visuals[selected]));};
  const head=el('div',null,'inspector-head'),done=el('button','Done','text-button');done.type='button';done.addEventListener('click',()=>{const index=selected;selected=null;renderDesign();focusBox(index);});
  head.append(el('h3',info.label),done);target.append(head);
  const palette=el('div',null,'palette');palette.setAttribute('role','group');palette.setAttribute('aria-label','Visual type');
  for(const item of catalog.visuals){
    const button=el('button',null,'palette-item'+(item.key===visual.kind?' active':''));button.type='button';button.title=item.label;button.setAttribute('aria-label',item.label);button.setAttribute('aria-pressed',String(item.key===visual.kind));button.innerHTML=iconSvg(item.key,'currentColor');
    button.addEventListener('click',()=>update(next=>{next.kind=item.key;for(const name of ['category','measure','measure2'])if(!item.fields.includes(name))next[name]=null;
      if(item.required.includes('category')&&!next.category)next.category=guessCategory(next.table);
      if(item.required.includes('measure')&&!next.measure)next.measure=guessMeasure(value,next.table);}));
    palette.append(button);
  }
  target.append(el('span','Visual type','field-label'),palette);
  if(info.note)target.append(el('p',info.note,'helper note'));
  const title=el('input');title.value=visual.title;title.maxLength=80;title.addEventListener('change',()=>update(next=>{next.title=title.value.trim()||info.label;}));field(target,'Title',title);
  if(value.tables.length>1)field(target,'Table',choose(value.tables.map(item=>[item.name,item.name]),visual.table,name=>update(next=>{next.table=name;next.category=null;next.columns=[];}),'Table'));
  for(const name of info.fields){
    const needed=info.required.includes(name),label=info.labels[name];
    const options=name==='category'?(cleaned[visual.table]?.columns||[]).map(column=>[column,column]):value.measures.map(item=>[item.name,item.name]);
    field(target,label+(needed?'':' (optional)'),choose([['',needed?'Choose…':'None'],...options],visual[name]||'',choice=>update(next=>{next[name]=choice||null;}),label));
  }
  const colors=el('div',null,'color-row');
  if(!NOFILL.has(visual.kind)&&!TEXTBOXES.has(visual.kind))colors.append(colorField('Chart color',visual.color,theme.colors[0],color=>update(next=>{next.color=color;}),theme.colors));
  colors.append(colorField('Box background',visual.background,theme.card,color=>update(next=>{next.background=color;}),[theme.card,theme.page]));
  target.append(colors);
  const actions=el('div',null,'actions'),copy=el('button','Duplicate','secondary'),remove=el('button','Delete','secondary danger');copy.type=remove.type='button';
  copy.disabled=page.visuals.length>=MAX_VISUALS;
  copy.addEventListener('click',()=>{const spot=freeSpot(page.visuals,visual.w,visual.h);editDesign(next=>next.pages[designPage].visuals.push({...visual,...spot,title:visual.title+' (copy)'}));selected=currentVisuals().length-1;renderDesign();});
  remove.addEventListener('click',()=>removeVisual(selected));
  actions.append(copy,remove);target.append(actions);
}
$('add-visual').addEventListener('click',addVisual);
$('picture-input').addEventListener('change',event=>{const file=event.target.files[0];event.target.value='';if(!file)return;
  if(!/^image\/(png|jpeg)$/.test(file.type)){notice('Choose a PNG or JPG picture.',true);return;}if(file.size>5*1024*1024){notice('Choose a picture under 5 MB.',true);return;}
  task('Adding your picture\u2026',async()=>{const result=await json('/design/image?project_id='+project.id,file);editDesign(value=>{const page=value.pages[designPage];page.image=result.name;page.transparency=page.transparency||20;});return 'Picture added. Boxes are slightly see-through so it shows; change that under the page settings.';});});
$('design-ai').addEventListener('click',()=>task('Designing your report\u2026',async()=>{snapshot();const result=await json('/design/suggest',{...ids(),model_plan:design(),privacy:privacy()});$('model-json').value=pretty(result.model_plan);selected=null;designPage=0;renderModel();controls();return (result.notes||[]).join(' ')+' Review it, then select Save design.';}));
$('design-theme').addEventListener('change',()=>editDesign(value=>{value.theme=$('design-theme').value;}));
$('design-save').addEventListener('click',()=>task('Saving your design…',async()=>{await saveDesign();return 'Design saved. Open it in Power BI or download the ZIP below.';}));
$('design-arrange').addEventListener('click',()=>task('Arranging this page…',async()=>{
  snapshot();const value=design();for(const visual of value.pages[designPage].visuals){delete visual.x;delete visual.y;delete visual.w;delete visual.h;}
  const modeled=await json('/model',{...ids(),model_plan:value,privacy:privacy()});run=modeled.run;$('model-json').value=pretty(modeled.model_plan);selected=null;renderModel();await refreshRuns();
}));
showTab(preference('tab')||'clean');
