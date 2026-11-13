"use strict";
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="sheetforge-token"]').content;
const el = (tag, value, cls) => { const node=document.createElement(tag); if(value!=null)node.textContent=value; if(cls)node.className=cls; return node; };
let project=null, run=null, profile=null, cleaned={}, busy=false, pendingFile=null;
const privacy=()=>document.querySelector('input[name="privacy"]:checked').value;
const ids=()=>({project_id:project.id,run_id:run?.id || null});
const pretty=value=>JSON.stringify(value,null,2);
function preference(key,value) { try { if(value===undefined)return localStorage.getItem('pivotra.'+key); localStorage.setItem('pivotra.'+key,value); } catch {} }
function setTheme(theme) { document.documentElement.dataset.theme=theme; $('theme-toggle').textContent=theme==='dark'?'☀':'☾'; $('theme-toggle').setAttribute('aria-label','Switch to '+(theme==='dark'?'light':'dark')+' theme'); preference('theme',theme); }
setTheme(preference('theme') || (matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'));
$('theme-toggle').addEventListener('click',()=>setTheme(document.documentElement.dataset.theme==='dark'?'light':'dark'));
async function api(path,body,options={}) {
  const headers={'X-SheetForge-Token':token,...options.headers};
  if(body!==undefined && !(body instanceof File)){headers['Content-Type']='application/json';body=JSON.stringify(body);}
  const response=await fetch(path,{method:body===undefined?'GET':'POST',body,...options,headers});
  if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error((data.detail||'The request failed.')+(data.errors?' '+data.errors.map(e=>e.msg).join(' '):''));}
  return response;
}
async function json(path,body){return(await api(path,body)).json();}
function notice(message,error=false){$('notice').hidden=!message;$('notice').textContent=message;$('notice').classList.toggle('error',error);}
function controls(){
  for(const item of document.querySelectorAll('button,input,select,textarea'))item.disabled=busy;
  $('build').disabled=busy||(!pendingFile&&!project);
  $('make-plan').disabled=busy||!project;
  $('apply-plan').disabled=busy||!project||!$('cleaning-json').value||!planDirty();
  const ready=run&&CHECKED_STATES.includes(run.status),cleanedReady=run&&CLEANED_STATES.includes(run.status);
  $('apply-quality').disabled=busy||!qualityReady();$('check-quality').disabled=busy||!run||!cleanedReady&&run.status!=='planned';
  for(const id of ['download-quality','download-quality-excel'])$(id).disabled=busy||!cleanedReady;
  $('make-model').disabled=busy||!ready;$('review-model').disabled=busy||!ready||!$('model-json').value;
  const saved=['modeled','generated'].includes(run?.status);$('download').disabled=busy||!saved;$('open-powerbi').disabled=busy||!saved;if(typeof studioControls==='function')studioControls();$('download-excel').disabled=busy||!cleanedReady;
  $('run-status').textContent=run?.status || 'Uploaded';document.body.setAttribute('aria-busy',String(busy));
}
async function task(message,work){if(busy)return;busy=true;controls();notice(message);try{notice((await work())||'');}catch(error){notice(error.message,true);if($('settings').open)$('settings-notice').textContent=error.message;}finally{busy=false;controls();}}
function table(target,columns,rows){const node=el('table'),head=el('thead'),tr=el('tr');columns.forEach(c=>tr.append(el('th',c)));head.append(tr);node.append(head);const body=el('tbody');for(const row of rows){const item=el('tr');for(const value of row)item.append(el('td',value==null?'—':value));body.append(item);}node.append(body);$(target).replaceChildren(node);}
function before(){const name=$('sheet').value||Object.keys(profile?.sheets||{})[0],sheet=profile?.sheets[name];if(!sheet)return;$('profile-stats').replaceChildren(el('span',sheet.shape[0]+' rows'),el('span',sheet.shape[1]+' columns'),el('span',sheet.blocks.length+' tables detected'));table('before',['Row',...Array.from({length:sheet.shape[1]},(_,i)=>'Column '+(i+1))],sheet.sample.slice(0,20).map(row=>[row.row+1,...row.values]));}
function renderAfter(){const data=cleaned[$('table-select').value];if(!data){$('after').replaceChildren(el('p','Clean your workbook to see the difference.','helper'));$('after-stats').replaceChildren();return;}$('after-stats').replaceChildren(el('span',data.row_count+' usable rows'),el('span',data.columns.length+' columns'),el('span','First 30 records'));table('after',data.columns,data.rows.map(row=>data.columns.map(c=>row[c])));}
function after(data){cleaned=data.tables||{};$('table-select').replaceChildren(...Object.keys(cleaned).map(name=>new Option(name,name)));renderAfter();$('audit').replaceChildren(...(data.steps||[]).map(step=>el('li',step.table+' · '+step.description)));$('warnings').textContent=(data.warnings||[]).join(' ');}
function stepper(label,value,min,change){
  const wrap=el('div',null,'stepper'),minus=el('button','\u2212','stepper-btn'),plus=el('button','+','stepper-btn'),input=el('input');
  minus.type='button';plus.type='button';minus.setAttribute('aria-label','Decrease '+label);plus.setAttribute('aria-label','Increase '+label);
  input.type='number';input.min=String(min);input.step='1';input.value=value;input.setAttribute('aria-label',label);
  const set=next=>{next=Math.max(min,Math.round(Number(next)||min));input.value=next;change(next);};
  minus.addEventListener('click',()=>set(Number(input.value)-1));plus.addEventListener('click',()=>set(Number(input.value)+1));input.addEventListener('change',()=>set(input.value));
  wrap.append(minus,input,plus);return wrap;
}
function toggle(label,checked,change){const wrap=el('label',null,'switch'),input=el('input');input.type='checkbox';input.checked=checked;input.setAttribute('aria-label',label);input.addEventListener('change',()=>change(input.checked));wrap.append(input,el('span',null,'switch-track'));return wrap;}
function miniSheet(kind){const grid=el('span',null,'mini-sheet');grid.setAttribute('aria-hidden','true');for(let r=0;r<4;r++)for(let c=0;c<4;c++){const on=kind==='titles'?r===0:kind==='data'?r>0:c===0;grid.append(el('i',null,on?'on':''));}return grid;}
function savedSpecs(){try{const plan=JSON.parse(run?.cleaning_plan_json||'null');return plan?[plan,...(plan.tables_split||[])]:[];}catch{return [];}}
function planDirty(){const saved=run?.cleaning_plan_json;if(!saved)return !!$('cleaning-json').value;try{return pretty(JSON.parse(saved))!==$('cleaning-json').value;}catch{return true;}}
function renderPlan(){
  const target=$('plan-steps');target.replaceChildren();let plan;
  if(!$('cleaning-json').value){target.append(el('p','Build a project to see how we read your sheet.','helper'));return;}
  try{plan=JSON.parse($('cleaning-json').value);}catch{target.append(el('p','The advanced editor contains invalid JSON. Fix it, or select Detect again.','helper'));return;}
  const commit=()=>{$('cleaning-json').value=pretty(plan);renderPlan();controls();},dirty=planDirty(),saved=savedSpecs();
  for(const spec of [plan,...(plan.tables_split||[])]){
    const original=saved.find(item=>item.table_name===spec.table_name)||spec,rows=cleaned[spec.table_name]?.row_count;
    const card=el('article',null,'layout-card'),head=el('div',null,'layout-head'),title=el('div');
    title.append(el('h3',spec.table_name),el('p','Sheet \u201c'+spec.sheet_name+'\u201d \u00b7 titles in row '+(spec.header_row_index+1)+' \u00b7 data starts on row '+(spec.data_start_row+1)+(rows!=null?' \u00b7 '+rows.toLocaleString()+' rows':''),'helper'));
    head.append(title,el('span',dirty?'Changed \u00b7 apply to update':'\u2713 Looks good',dirty?'layout-chip changed':'layout-chip'));
    const grid=el('div',null,'layout-grid'),notes=[];
    const setting=(name,help,control,picture)=>{const item=el('div',null,'layout-setting'),text=el('div',null,'layout-text');text.append(el('strong',name),el('p',help,'helper'));item.append(miniSheet(picture),text,control);grid.append(item);};
    setting('Which row has your column titles?','The row with names like Date, Customer or Amount. Usually row 1.',stepper('Header row for '+spec.table_name,spec.header_row_index+1,1,value=>{spec.header_row_index=value-1;spec.multi_row_header_rows=[];spec.data_start_row=Math.max(spec.data_start_row,value);commit();}),'titles');
    setting('Which row does your data start on?','The first row with real values, right under the titles. Usually row 2.',stepper('First data row for '+spec.table_name,spec.data_start_row+1,spec.header_row_index+2,value=>{spec.data_start_row=value-1;commit();}),'data');
    setting('Are your titles down the left side?','Only for sheets where the titles run down the first column instead of across the top. Most files: leave this off.',toggle('Transpose '+spec.table_name,spec.orientation==='transposed',on=>{spec.orientation=on?'transposed':'normal';spec.data_end_row=on?spec.bounds.column_end-spec.bounds.column_start:spec.bounds.row_end-spec.bounds.row_start;commit();}),'side');
    const totals=original.totals_rows_to_drop.length?original.totals_rows_to_drop:spec.totals_rows_to_drop;
    if(totals.length)setting('Skip the total rows?','We found '+totals.length+' row(s) like \u201cTotal\u201d. Skipping them stops values from being counted twice.',toggle('Skip total rows in '+spec.table_name,spec.totals_rows_to_drop.length>0,on=>{spec.totals_rows_to_drop=on?[...totals]:[];commit();}),'data');
    else setting('Any total rows to skip?','We looked for rows like “Total” or “Subtotal” and found none, so nothing gets counted twice.',el('span','✓ None found','layout-ok'),'data');
    const wide=original.unpivot_columns.length?original.unpivot_columns:spec.unpivot_columns;
    if(wide.length)setting('Stack the month columns?','You have '+wide.length+' columns like \u201c'+wide[0]+'\u201d. Stacking them into one Period column and one Value column makes better charts.',toggle('Unpivot '+spec.table_name,spec.unpivot_columns.length>0,on=>{spec.unpivot_columns=on?[...wide]:[];commit();}),'titles');
    card.append(head,grid);
    if(notes.length)card.append(el('p','\u2713 '+notes.join(' \u00b7 '),'layout-note'));
    target.append(card);
  }
}
function explainMeasure(dax){
  const ref=/^(SUM|AVERAGE|MIN|MAX|DISTINCTCOUNT)\(\s*'(?:[^']|'')+'\[((?:[^\]]|\]\])+)\]\s*\)$/i.exec(dax.trim());
  if(/^COUNTROWS/i.test(dax.trim()))return 'Counts every row';
  if(/^DIVIDE/i.test(dax.trim()))return 'Divides one number by another';
  if(!ref)return 'Custom calculation';
  const column='\u201c'+ref[2].replace(/\]\]/g,']')+'\u201d';
  return {SUM:'Adds up all values in '+column,AVERAGE:'Average of '+column,MIN:'Smallest value in '+column,MAX:'Largest value in '+column,DISTINCTCOUNT:'Number of different values in '+column}[ref[1].toUpperCase()];
}
function renderModel(){
  const target=$('model-preview');target.replaceChildren();let model;
  try{model=JSON.parse($('model-json').value);}catch{
    // Say why this panel is empty: usually the quality check is waiting for the user's choices.
    const box=el('div',null,'model-waiting');
    if(run?.status==='needs_fix')box.append(el('strong','Paused at the data quality check'),el('p','You chose to fix some issues yourself. Fix your file and upload it again to continue.','helper'));
    else if(quality&&!CHECKED_STATES.includes(run?.status)){
      const go=el('button','Go to data quality','secondary');go.type='button';go.addEventListener('click',()=>{showTab('clean');$('quality-panel').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});});
      const text=el('div');text.append(el('strong','Waiting for your data quality choices'),el('p','Choose what to do with each data quality issue above, then select Apply and continue. Your Power BI report is built right after.','helper'));
      box.append(text,go);
    }
    else box.append(el('p','Build a project to see what your report will contain.','helper'));
    target.append(box);if(typeof renderDesign==='function')renderDesign();return;
  }
  const single=model.tables.length===1,short=name=>single?name.replace(/^.*? \u00b7 /,''):name;
  const group=(title,hint,cls)=>{const box=el('section',null,'model-group '+(cls||''));box.append(el('h3',title),el('p',hint,'helper'));target.append(box);return box;};
  const item=(box,name,detail,extra)=>{const card=el('div',null,'model-item');card.append(el('strong',name),el('span',detail));if(extra){const code=el('code',extra);card.append(code);}box.append(card);return card;};
  const numbers=group('Numbers in your report','These become the number cards and chart values in Power BI.','model-numbers');
  const list=el('div',null,model.measures.length<=4?'model-list single':'model-list');numbers.append(list);
  const draw=(box,measure)=>item(box,short(measure.name),explainMeasure(measure.dax),measure.dax);
  model.measures.slice(0,6).forEach(measure=>draw(list,measure));
  if(model.measures.length>6){const more=el('details',null,'model-more');more.append(el('summary','Show '+(model.measures.length-6)+' more'));const rest=el('div',null,'model-list');model.measures.slice(6).forEach(measure=>draw(rest,measure));more.append(rest);numbers.append(more);}
  const side=el('div',null,'model-side');target.append(side);
  const place=box=>{side.append(box);return box;};
  const tables=place(group('Your tables',single?'All your data is in one table.':'How your tables are used.'));
  for(const table of model.tables)item(tables,table.name,table.role==='fact'?'Main data \u00b7 the rows your numbers are counted from':'Lookup list \u00b7 names and details other tables point to');
  // A links card only matters with several tables; one table gets a single line instead.
  if(single)tables.append(el('p','\u2713 One table, so nothing needs linking.','model-empty'));
  else{
    const links=place(group('Links between tables','Lets one chart combine data from several tables.'));
    if(!model.relationships.length)links.append(el('p','No matching ID columns were found between your tables.','model-empty'));
    for(const link of model.relationships)item(links,link.from_table+' \u2192 '+link.to_table,'Matched on '+link.from_column+(link.from_column===link.to_column?'':' = '+link.to_column)+' \u00b7 many rows match one');
  }
  const pages=place(group('Report pages','What you will see when you open the report.'));
  const words={card:['number card','number cards'],line:['trend chart','trend charts'],bar:['bar chart','bar charts'],donut:['share donut','share donuts'],slicer:['filter','filters'],table:['records table','records tables']};
  for(const page of model.pages||[]){
    const counts={};for(const visual of page.visuals)counts[visual.kind]=(counts[visual.kind]||0)+1;
    item(pages,page.title,Object.entries(counts).map(([kind,n])=>n+' '+(words[kind]||[kind,kind])[n===1?0:1]).join(', '));
  }
  const left=el('div',null,'model-side');left.append(numbers);target.prepend(left);
  if(typeof renderDesign==='function')renderDesign();
}
async function refreshProjects(){const data=await json('/projects');$('project-list').replaceChildren(...data.projects.map(item=>{const button=el('button',null,'project-item');button.append(el('strong',item.name),el('small',new Date(item.created_at).toLocaleDateString()));button.addEventListener('click',()=>task('Opening project…',async()=>{await openProject(item);$('projects').close();}));return button;}));if(!data.projects.length)$('project-list').append(el('p','Your saved projects will appear here.','helper'));return data.projects;}
async function refreshRuns(){const data=await json('/runs?project_id='+project.id);$('run-select').replaceChildren(...data.runs.map(item=>new Option(new Date(item.created_at).toLocaleString()+' · '+item.status,item.id)));if(run)$('run-select').value=run.id;return data.runs;}
async function setRun(item){run=item;$('cleaning-json').value=item?.cleaning_plan_json?pretty(JSON.parse(item.cleaning_plan_json)):'';$('model-json').value=item?.model_plan_json?pretty(JSON.parse(item.model_plan_json)):'';after(item?await json('/preview?run_id='+item.id):{});loadQuality(item?await json('/quality?run_id='+item.id):null);$('quality-stop').hidden=item?.status!=='needs_fix';renderPlan();renderModel();}
async function openProject(item){project=item;if(typeof resetFormulas==='function')resetFormulas();preference('project',item.id);profile=await json('/profile?project_id='+item.id);document.body.classList.add('has-project');$('empty').hidden=true;$('workspace').hidden=false;$('project-name').textContent=item.name;if(!pendingFile){$('file-placeholder').textContent=item.name;$('file-caption').textContent='Your saved workbook is ready to build again.';}$('source-description').textContent='Refresh source: '+item.source_path;$('sheet').replaceChildren(new Option('All worksheets',''),...Object.keys(profile.sheets).map(name=>new Option(name,name)));before();await setRun((await refreshRuns())[0]||null);await refreshProjects();}
function selectFile(file){if(!/\.(xlsx|csv)$/i.test(file.name))throw new Error('Choose an .xlsx or .csv file.');if(file.size>8*1024*1024)throw new Error('Choose a file under 8 MB.');pendingFile=file;$('file-placeholder').textContent=file.name;$('file-caption').textContent='Ready when you are. Review your privacy mode, then build.';controls();}
$('drop-zone').addEventListener('click',()=>$('upload').click());$('upload').addEventListener('change',event=>{try{if(event.target.files[0])selectFile(event.target.files[0]);notice('');}catch(error){notice(error.message,true);}event.target.value='';});
for(const name of ['dragenter','dragover'])$('drop-zone').addEventListener(name,event=>{event.preventDefault();if(!busy)$('drop-zone').classList.add('drag-over');});
for(const name of ['dragleave','drop'])$('drop-zone').addEventListener(name,event=>{event.preventDefault();$('drop-zone').classList.remove('drag-over');if(name==='drop'&&!busy){try{if(event.dataTransfer.files[0])selectFile(event.dataTransfer.files[0]);}catch(error){notice(error.message,true);}}});
for(const button of document.querySelectorAll('[data-sample]'))button.addEventListener('click',()=>task('Preparing sample workbook…',async()=>{const response=await api('/samples/'+button.dataset.sample);selectFile(new File([await response.blob()],button.textContent.replace(' ↗','')+'.xlsx'));}));
async function applyReviewed(){const planned=await json('/plan',{...ids(),cleaning_plan:JSON.parse($('cleaning-json').value),privacy:privacy()});run=planned.run;const result=await json('/apply',ids());run=result.run;after(result);renderPlan();$('model-json').value='';renderModel();}
async function ensureApplied(){if(!CLEANED_STATES.includes(run?.status))await applyReviewed();if(!await qualityGate())throw new Error(QMSG);}
let quality=null;
const CLEANED_STATES=['applied','checked','needs_fix','modeled','generated'],CHECKED_STATES=['checked','modeled','generated'];
const QLABEL={formatting:'Formatting',missing:'Missing values',type_consistency:'Wrong types',impossible_values:'Impossible values',exact_duplicates:'Exact duplicates',near_duplicates:'Near-duplicates'};
const ALABEL={fix_for_me:'Fix for me',i_will_fix:"I'll fix it",ignore:'Ignore'};
const SLABEL={normalize:'Normalize (lossless)',fill_mean:'Fill with the average',fill_median:'Fill with the median',fill_zero:'Fill with zero',fill_unknown:'Fill text with "Unknown"',ffill:'Copy the value above',drop:'Drop affected rows',nullify:'Clear the value',clamp:'Clamp to the limit',keep_first:'Merge: keep the first row',keep_latest:'Merge: keep the latest row',keep_max:'Merge: keep the largest numbers'};
const QMSG='Review the data quality check below, choose what to do with each issue, then select Apply and continue.';
const STOPMSG='Stopped so you can fix the file yourself. Download the report and cleaned Excel, then upload the fixed file.';
function choose(options,value,change,label){const node=el('select');for(const [v,l] of options)node.append(new Option(l,v));node.value=value||'';node.setAttribute('aria-label',label);node.addEventListener('change',()=>change(node.value));return node;}
function loadQuality(saved){
  if(!saved?.report){quality=null;renderQuality();return;}
  const decisions={};
  for(const [name,report] of Object.entries(saved.report)){
    decisions[name]={};
    for(const item of report.categories){const old=saved.decisions?.[name]?.[item.category]||{};decisions[name][item.category]={action:old.action||item.default_action||'',strategy:old.strategy||item.default_strategy,key:old.key||report.duplicate_key,confirmed:!!old.key};}
  }
  quality={report:saved.report,decisions};renderQuality();
}
function qualityIssues(){return !!quality&&Object.values(quality.report).some(report=>report.categories.length);}
function qualityReady(){
  if(!quality)return false;
  return Object.entries(quality.report).every(([name,report])=>report.categories.every(item=>{const d=quality.decisions[name][item.category];return d.action&&(d.action!=='fix_for_me'||item.category!=='near_duplicates'||(d.key.length&&d.confirmed));}));
}
function qualityPayload(){
  const result={};
  for(const [name,choices] of Object.entries(quality.decisions)){result[name]={};for(const [category,d] of Object.entries(choices)){const entry={action:d.action};if(d.action==='fix_for_me'){entry.strategy=d.strategy;if(category==='near_duplicates')entry.key=d.key;}result[name][category]=entry;}}
  return result;
}
function renderQuality(){
  const target=$('quality-tables');target.replaceChildren();$('quality-panel').hidden=!quality;
  if(!quality){controls();return;}
  let index=0;
  for(const [name,report] of Object.entries(quality.report)){
    target.append(el('h3',name+' \u00b7 '+report.rows+' rows','plan-table-title'));
    if(!report.categories.length){target.append(el('p','No quality issues found.','helper'));continue;}
    const flagged=report.columns.filter(c=>c.null_pct||c.placeholder_nulls||c.type_mismatches||c.formatting_issues||c.impossible_values);
    if(flagged.length){const holder=el('div',null,'table-wrap quality-columns');holder.id='quality-columns-'+(index++);target.append(holder);table(holder.id,['Column','Type','Missing %','Placeholders','Wrong type','Formatting','Impossible'],flagged.map(c=>[c.column,c.type,c.null_pct,c.placeholder_nulls,c.type_mismatches,c.formatting_issues,c.impossible_values]));}
    for(const item of report.categories){
      const d=quality.decisions[name][item.category],label=QLABEL[item.category],row=el('div',null,'quality-row'),text=el('div',null,'quality-text');
      text.append(el('strong',label+' \u00b7 '+item.count),el('p',item.detail,'helper'));
      if(item.category==='near_duplicates'&&report.near_duplicate_groups.length)text.append(el('p','For example: '+report.near_duplicate_groups.slice(0,3).map(g=>g.key.join(' / ')+' (rows '+g.rows.join(', ')+')').join('; '),'helper'));
      const fields=el('div',null,'quality-fields');
      fields.append(choose([['','Choose\u2026'],...Object.entries(ALABEL)],d.action,value=>{d.action=value;renderQuality();},'Action for '+label+' in '+name));
      if(d.action==='fix_for_me'&&item.strategies.length>1)fields.append(choose(item.strategies.map(v=>[v,SLABEL[v]]),d.strategy,value=>{d.strategy=value;},'Strategy for '+label+' in '+name));
      if(d.action==='fix_for_me'&&item.category==='near_duplicates'){
        const key=el('input');key.value=d.key.join(', ');key.setAttribute('aria-label','Duplicate key columns for '+name);key.addEventListener('change',()=>{d.key=key.value.split(',').map(v=>v.trim()).filter(Boolean);d.confirmed=false;renderQuality();});
        const confirm=el('label',null,'quality-confirm'),box=el('input');box.type='checkbox';box.checked=d.confirmed;box.addEventListener('change',()=>{d.confirmed=box.checked;controls();});confirm.append(box,document.createTextNode(' Rows with the same key are the same record'));
        fields.append(key,confirm);
      }
      row.append(text,fields);target.append(row);
    }
  }
  const needed=Object.entries(quality.report).reduce((sum,[name,report])=>sum+report.categories.filter(item=>{const d=quality.decisions[name][item.category];return !d.action||(d.action==='fix_for_me'&&item.category==='near_duplicates'&&!(d.key.length&&d.confirmed));}).length,0);
  const badge=$('quality-badge');badge.hidden=!qualityIssues();badge.textContent=needed?needed+(needed===1?' choice needed':' choices needed'):'✓ Ready to continue';badge.className=needed?'layout-chip changed':'layout-chip';
  controls();renderModel();
}
async function inspectQuality(){const result=await json('/quality/inspect',{...ids(),privacy:privacy()});loadQuality({report:result.report});$('quality-notes').textContent=(result.notes||[]).join(' ');$('quality-stop').hidden=true;return qualityIssues();}
async function applyQuality(){const result=await json('/quality',{...ids(),decisions:qualityPayload()});run=result.run;after(result);$('quality-stop').hidden=!result.stopped;await refreshRuns();return !result.stopped;}
async function qualityGate(){
  if(CHECKED_STATES.includes(run?.status))return true;
  if(await inspectQuality()){$('quality-panel').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});return false;}
  return applyQuality();
}
async function modelAndGenerate(){notice('Building your data model…');const modeled=await json('/model',{...ids(),privacy:privacy()});run=modeled.run;$('model-json').value=pretty(modeled.model_plan);renderModel();notice('Preparing your Power BI project…');run=(await json('/generate',ids())).run;await refreshRuns();}
$('build').addEventListener('click',()=>task('Inspecting your workbook…',async()=>{if(pendingFile){const uploaded=await json('/upload?filename='+encodeURIComponent(pendingFile.name),pendingFile);await openProject(uploaded.project);pendingFile=null;}const planned=await json('/plan',{...ids(),sheet:$('sheet').value||null,privacy:privacy()});run=planned.run;$('cleaning-json').value=pretty(planned.plan);renderPlan();await applyReviewed();if(!await qualityGate())return QMSG;await modelAndGenerate();$('workspace').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});}));
$('make-plan').addEventListener('click',()=>task('Suggesting a cleaning plan…',async()=>{const result=await json('/plan',{...ids(),sheet:$('sheet').value||null,privacy:privacy()});await setRun(result.run);await refreshRuns();}));
$('apply-plan').addEventListener('click',()=>task('Re-running your reviewed steps…',async()=>{await applyReviewed();if(!await qualityGate())return QMSG;await modelAndGenerate();}));
$('make-model').addEventListener('click',()=>task('Suggesting a model…',async()=>{await ensureApplied();const result=await json('/model',{...ids(),privacy:privacy()});run=result.run;$('model-json').value=pretty(result.model_plan);renderModel();await refreshRuns();}));
$('review-model').addEventListener('click',()=>task('Validating your edited model…',async()=>{const edited=JSON.parse($('model-json').value);await ensureApplied();run=(await json('/model',{...ids(),model_plan:edited,privacy:privacy()})).run;renderModel();await refreshRuns();}));
function attachmentName(response,fallback){const header=response.headers.get('content-disposition')||'',encoded=/filename\*=UTF-8''([^;]+)/i.exec(header),plain=/filename="([^"]+)"/i.exec(header);try{if(encoded)return decodeURIComponent(encoded[1]);}catch{}return plain?plain[1]:fallback;}
async function download(path,filename){const response=await api(path);filename=attachmentName(response,filename);const url=URL.createObjectURL(await response.blob()),link=el('a');link.href=url;link.download=filename;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);}
$('download').addEventListener('click',()=>task('Preparing Power BI download…',async()=>{await saveDesign();return download('/download?run_id='+run.id,'Pivotra-powerbi.zip');}));$('download-excel').addEventListener('click',()=>task('Preparing your cleaned workbook…',()=>download('/download/excel?run_id='+run.id,'Pivotra-cleaned.xlsx')));
$('path-form').addEventListener('submit',event=>{event.preventDefault();task('Opening local workbook…',async()=>{await openProject((await json('/upload',{source_path:$('source-path').value})).project);});});
$('sheet').addEventListener('change',before);$('table-select').addEventListener('change',renderAfter);$('cleaning-json').addEventListener('input',()=>{renderPlan();controls();});$('model-json').addEventListener('input',()=>{renderModel();controls();});
$('run-select').addEventListener('change',()=>task('Opening saved run…',async()=>{const selected=$('run-select').value;await setRun((await json('/runs?project_id='+project.id)).runs.find(item=>item.id===selected));}));
$('new-project').addEventListener('click',()=>{project=run=profile=pendingFile=null;cleaned={};preference('project','');document.body.classList.remove('has-project');$('workspace').hidden=true;$('empty').hidden=false;$('file-placeholder').textContent='Drop an .xlsx or .csv here.';$('file-caption').textContent='Even a messy one is enough to start.';notice('');controls();window.scrollTo({top:0,behavior:'smooth'});});
$('projects-open').addEventListener('click',()=>task('Opening saved projects…',async()=>{await refreshProjects();$('projects').showModal();}));$('projects-close').addEventListener('click',()=>$('projects').close());
function privacyHelp(){const notes={local:'Your file stays on this device. Cloud providers are off; local Ollama is allowed.',masked:'Sample cell values are masked. Column names and statistics may be sent to your chosen provider.',sample:'Up to 20 first + 5 last rows, column names and statistics may be sent. Bulk data is never shared.'};$('privacy-help').textContent=notes[privacy()];}
for(const input of document.querySelectorAll('[name=privacy]'))input.addEventListener('change',privacyHelp);
async function modelMode(){const data=await json('/settings');$('mode').textContent=data.provider==='none'?'No-AI · offline':data.provider+' · '+data.model;return data;}
async function openSettings(provider){const data=await modelMode();$('provider').value=provider||data.provider;$('model-id').value=!provider||provider===data.provider?data.model:'';$('base-url').value=provider&&provider!==data.provider?'':data.base_url||'';$('api-version').value=data.api_version||'';$('api-key').value='';$('settings-notice').textContent='Choose a model ID and save. API keys stay in Windows Credential Manager.';$('settings').showModal();}
$('settings-open').addEventListener('click',()=>task('Opening model settings…',()=>openSettings()));$('settings').addEventListener('close',()=>{$('api-key').value='';});
async function discover(){const data=await json('/settings/ollama');$('ollama-models').replaceChildren(...data.models.map(name=>new Option(name,name)));$('ollama-status').textContent=data.available?'Available local models are listed in the model field.':'Ollama was not detected at localhost:11434.';return data;}
$('quick-provider').addEventListener('change',()=>task('Updating provider…',async()=>{const selected=$('quick-provider').value,current=await modelMode();if(selected==='none'){await json('/settings',{provider:'none',model:''});await modelMode();}else if(selected==='auto'){const data=await discover();if(data.models.length){await json('/settings',{provider:'ollama',model:data.models[0],base_url:'http://localhost:11434'});$('quick-provider').value='ollama';await modelMode();}else{await openSettings('ollama');$('settings-notice').textContent='Ollama was not detected. Start Ollama, or choose another provider. No-AI mode still works.';}}else if(current.provider!==selected)await openSettings(selected);}));
$('provider').addEventListener('change',()=>{if($('provider').value==='ollama')task('Checking local Ollama…',async()=>{await discover();$('base-url').value='http://localhost:11434';});});
$('settings-form').addEventListener('submit',event=>{event.preventDefault();task('Saving model settings…',async()=>{await json('/settings',{provider:$('provider').value,model:$('model-id').value,base_url:$('base-url').value||null,api_version:$('api-version').value||null,api_key:$('api-key').value||null});$('api-key').value='';$('quick-provider').value=$('provider').value;await modelMode();$('settings-notice').textContent='Settings saved. Privacy mode still controls whether cloud requests are allowed.';});});
$('test-model').addEventListener('click',()=>task('Testing saved connection…',async()=>{$('settings-notice').textContent=(await json('/settings/test',{})).message;}));
task('Opening Pivotra…',async()=>{const settings=await modelMode();$('quick-provider').value=settings.provider;privacyHelp();const projects=await refreshProjects(),saved=projects.find(item=>item.id===preference('project'));if(saved)await openProject(saved);});

$('apply-quality').addEventListener('click',()=>task('Applying your choices\u2026',async()=>{if(!await applyQuality())return STOPMSG;await modelAndGenerate();}));
$('check-quality').addEventListener('click',()=>task('Checking data quality\u2026',async()=>{if(!CLEANED_STATES.includes(run?.status))await applyReviewed();if(!await inspectQuality()){await applyQuality();return 'No quality issues found.';}return QMSG;}));
$('download-quality').addEventListener('click',()=>task('Preparing the quality report\u2026',()=>download('/download/quality?run_id='+run.id,'Pivotra-quality-report.json')));
$('download-quality-excel').addEventListener('click',()=>task('Preparing your cleaned workbook\u2026',()=>download('/download/excel?run_id='+run.id,'Pivotra-cleaned.xlsx')));
$('reupload').addEventListener('click',()=>{scrollTo({top:0,behavior:'smooth'});$('upload').click();});

$('open-powerbi').addEventListener('click',()=>task('Opening in Power BI…',async()=>{await saveDesign();const result=await json('/open/powerbi',ids());return result.opened?'Opened in Power BI Desktop. Your project is saved in '+result.folder:'Saved to '+result.folder+'. Open the .pbip file there with Power BI Desktop.';}));
