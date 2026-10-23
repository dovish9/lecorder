// Draft-only form state; credentials are never persisted in browser storage.
export function setupAISettings() {
  const $ = (s) => document.querySelector(s);
  const dialog = $('#systemDialog'), form = $('#systemForm');
  const panels = $('#aiSettingsPanel'), paths = $('#pathSettingsPanel');
  const history = $('#approvalHistory'), footer = form.querySelector('footer');
  let historyOffset = 0, historyGeneration = 0;
  const message = $('#systemMessage'), save = $('#saveSystemButton'), hint = $('#systemSaveHint');
  let capabilities = {}, loading = false;
  const saves = new Map();
  let area = 'paths', response, draft, key = '', busy = false, checking = false, generation = 0;
  const providers = {speech: [['whisper_cpp','로컬 · whisper.cpp'],['openai','OpenAI']], llm: [['llama_cpp','로컬 · llama.cpp'],['openai','OpenAI'],['claude','Claude'],['gemini','Gemini']]};
  providers.speech[0][1] = '로컬 · whisper.cpp'; providers.llm[0][1] = '로컬 · llama.cpp';
  const presets = {
    openai: {base_url:'https://api.openai.com/v1', model:''},
    claude: {base_url:'https://api.anthropic.com/v1', model:''},
    gemini: {base_url:'https://generativelanguage.googleapis.com/v1beta', model:''},
  };
  const models = {openai:['gpt-4.1-mini','gpt-4.1'], claude:['claude-sonnet-4-5'], gemini:['gemini-2.5-flash','gemini-2.5-pro']};
  const element = (tag, cls, text) => {const e=document.createElement(tag); if(cls)e.className=cls; if(text!==undefined)e.textContent=text; return e;};
  async function api(url, method='GET', body, signal) {
    const result = await fetch(url, {signal: AbortSignal.any([AbortSignal.timeout(url.endsWith('/check')?(Number(capabilities.settings_check_seconds||55)+5)*1000:15000),...(signal?[signal]:[])]), method, headers: {'Content-Type':'application/json'}, body: body===undefined ? undefined : JSON.stringify(body)});
    const data = await result.json(); if(!result.ok) throw new Error(data.error || '요청 실패'); return data;
  }
  function invalidate(){ saves.delete(area); ++generation; checking=false; save.disabled=busy; message.textContent='설정 저장을 누르면 연결을 검증한 뒤 저장합니다.'; }
  function picker(label, options, value, onChange) {
    const wrap=element('div','ai-field'), title=element('span','ai-field-label',label), details=element('details','upload-course-picker ai-picker');
    const summary=element('summary','',options.find(o=>o[0]===value)?.[1] || value || '모델 선택');
    summary.setAttribute('aria-label',label); const list=element('div','ai-picker-options');
    for(const [id,name] of options){const b=element('button',id===value?'selected':'',name); b.type='button'; b.addEventListener('click',()=>{details.open=false;onChange(id);});list.append(b);}
    details.addEventListener('keydown',e=>{if(e.key==='Escape'&&details.open){e.preventDefault();e.stopPropagation();details.open=false;summary.focus();} if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();details.open=true;const items=[...list.children];const i=items.indexOf(document.activeElement);items[(i+(e.key==='ArrowDown'?1:-1)+items.length)%items.length].focus();}});
    if(busy){summary.tabIndex=-1;details.addEventListener('click',e=>{e.preventDefault();e.stopImmediatePropagation();},true);}
    details.append(summary,list);wrap.append(title,details);return wrap;
  }
  function field(label, prop, type='text') {
    const wrap=element('label','ai-field'), title=element('span','ai-field-label',label), input=element('input','ai-input');
    input.disabled=busy;input.type=type; input.autocomplete='off';input.spellcheck=false;input.setAttribute('autocapitalize','off'); input.name='lecorder-ai-'+prop;
    input.value=prop==='api_key'?key:draft[prop]||'';
    input.addEventListener('input',()=>{if(prop==='api_key')key=input.value;else {draft[prop]=input.value;if(prop==='binary')panels.querySelectorAll('input[name="lecorder-ai-binary"]').forEach(other=>{if(other!==input)other.value=input.value;});}invalidate();});wrap.append(title,input);return wrap;
  }
  const sectionCopy = {
    paths: ['경로', '프로젝트와 음성 변환 결과를 저장할 위치를 관리합니다.'],
    speech: ['음성 변환', '녹음을 마친 뒤 전체 음성을 텍스트로 바꿀 모델을 설정합니다.'],
    llm: ['LLM', '음성 변환 검수와 강의노트 분석에 사용할 모델을 설정합니다.'],
    history: ['승인한 검토', '승인한 수정의 전후 문맥과 발화 구간을 자료로 보관합니다.'],
  };
  function render() {
    const task = saves.get(area);
    busy = loading || Boolean(task?.pending);
    checking = task?.phase === 'checking';
    if(task)message.textContent=task.message;
    $('#systemDialogTitle').textContent=sectionCopy[area][0];
    $('#settingsDescription').textContent=sectionCopy[area][1];
    paths.hidden=area!=='paths'; panels.hidden=!['speech','llm'].includes(area);
    history.hidden=area!=='history'; footer.hidden=area==='history';
    document.querySelectorAll('[data-ai-tab]').forEach(b=>{const active=b.dataset.aiTab===area;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;});
    hint.textContent='저장 후 상단 알림의 재시작 버튼을 누르면 변경한 설정을 적용합니다.';
    save.textContent=area==='paths'?'경로 저장':checking?'검증 중…':'설정 저장'; save.disabled=busy || (area!=='paths'&&!draft);
    panels.replaceChildren(); if(!['speech','llm'].includes(area)||!draft)return;
    const local=['llama_cpp','whisper_cpp'].includes(draft.provider);

    panels.append(picker('제공자',providers[area],draft.provider,id=>{
      const live=area==='speech'?Object.fromEntries(['binary','live_base_url','live_model','live_model_path','live_vad_path'].map(k=>[k,draft[k]??response.defaults.speech[k]])):{};
      draft=id===response.defaults[area].provider?structuredClone(response.defaults[area]):{provider:id,managed:false,...presets[id],model:area==='speech'?'whisper-1':''};Object.assign(draft,live);key='';invalidate();render();
    }));
    if(local){const label=element('label','ai-managed');const check=element('input');check.disabled=busy;check.type='checkbox';check.setAttribute('role','switch');check.checked=draft.managed;check.addEventListener('change',()=>{draft.managed=check.checked;invalidate();render();});label.append(element('span','','로컬 서버 자동 관리'),check);panels.append(label);}
    if(!local && area==='llm')panels.append(picker('모델 프리셋',[...(models[draft.provider]||[]).map(m=>[m,m]),['','직접 입력']],draft.model,id=>{draft.model=id;invalidate();render();}));
    panels.append(field('모델 ID','model'),field('서버 URL','base_url','url'));
    if(draft.managed){
      panels.append(field('서버 실행 파일','binary'),field('모델 파일','model_path'),field(area==='speech'?'VAD 모델 파일':'이미지 projector 파일',area==='speech'?'vad_path':'projector_path'));
    }else{
      panels.append(field('API 키','api_key','password'));
      const keyline=element('div','ai-key-line');keyline.append(element('span','',draft.key_present?'보안 저장소에 키가 저장되어 있습니다.':`키는 ${capabilities.credential_store || '운영체제 보안 저장소'}에 보관됩니다.`));
      const del=element('button','ai-text-button','저장된 키 삭제');del.disabled=busy;del.type='button';del.addEventListener('click',async()=>{if(!confirm('이 서버에 저장된 API 키를 삭제할까요?'))return;try{await api('/api/ai-settings/'+area+'/key','DELETE',{config:draft});draft.key_present=false;key='';invalidate();render();message.textContent='API 키를 삭제했습니다.';}catch(e){message.textContent=e.message;}});keyline.append(del);panels.append(keyline);
    }
    panels.append(element('p','ai-privacy',local&&draft.managed?'음성과 강의노트가 이 컴퓨터 안에서 처리됩니다.':`선택한 서버로 ${area==='speech'?'음성을':'변환 텍스트과 강의노트 텍스트·이미지를'} 전송합니다. 외부 API 요금이 발생할 수 있으며, 요청 후 취소해도 이미 처리된 요청은 과금될 수 있습니다.`));
    const actions=element('div','ai-settings-actions'), reset=element('button','','기본값으로 초기화');reset.type='button';
    reset.disabled=busy;
    reset.addEventListener('click',()=>{draft=structuredClone(response.defaults[area]);key='';invalidate();render();message.textContent='로컬 기본값으로 되돌렸습니다. 저장 후 재시작하면 적용됩니다.';});
    actions.append(reset);
    const connection=element('div','settings-row-group');
    const files=element('details','settings-local-files');
    files.append(element('summary','','로컬 파일'));
    const fileRows=element('div','settings-file-rows');
    for(const row of [...panels.children]) {
      if(row.matches('.ai-field,.ai-managed,.ai-key-line')) {
        const input=row.querySelector('input');
        if(draft.managed && input && ['lecorder-ai-binary','lecorder-ai-model_path','lecorder-ai-vad_path','lecorder-ai-projector_path'].includes(input.name)) fileRows.append(row);
        else connection.append(row);
      }
    }
    panels.prepend(connection);
    if(fileRows.childElementCount){files.append(fileRows);connection.append(files);}
    if(area==='speech') {
      const liveSection=element('section','settings-live-section');
      liveSection.append(element('h3','','실시간 음성 변환'),element('p','ai-privacy','녹음 중 초안에 사용하는 로컬 모델입니다. 변경한 설정은 서버를 재시작하면 적용됩니다.'));
      const liveRows=element('div','settings-row-group');
      for(const prop of ['binary','live_base_url','live_model','live_model_path','live_vad_path']) draft[prop]??=response.defaults.speech[prop];
      const provider=picker('제공자',[['whisper_cpp','로컬 · whisper.cpp']],'whisper_cpp',()=>{});provider.querySelector('summary').setAttribute('aria-disabled','true');provider.querySelector('summary').tabIndex=-1;for(const event of ['click','keydown'])provider.addEventListener(event,e=>{e.preventDefault();e.stopImmediatePropagation();},true);
      const managed=element('label','ai-managed'),toggle=element('input');toggle.type='checkbox';toggle.checked=true;toggle.disabled=true;toggle.setAttribute('role','switch');managed.append(element('span','','로컬 서버 자동 관리'),toggle);
      const liveFiles=element('details','settings-local-files'),liveFileRows=element('div','settings-file-rows');
      liveFiles.append(element('summary','','로컬 파일'));
      liveFileRows.append(field('서버 실행 파일','binary'),field('모델 파일','live_model_path'),field('VAD 모델 파일','live_vad_path'));liveFiles.append(liveFileRows);
      liveRows.append(provider,managed,field('모델 ID','live_model'),field('서버 URL','live_base_url','url'),liveFiles);
      liveSection.append(liveRows);panels.insertBefore(liveSection,connection.nextSibling);
    }
    panels.append(actions);
  }
  for(const button of document.querySelectorAll('[data-ai-tab]')) {
    button.addEventListener('click',()=>{++generation;loading=false;busy=false;checking=false;area=button.dataset.aiTab;if(!response){void enterSettings(false);return;}draft=['paths','history'].includes(area)?null:structuredClone(saves.get(area)?.pending?saves.get(area).config:response?.settings[area]||{});key='';message.textContent='';render();if(area==='history'){historyOffset=0;void loadHistory();}});
    button.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();const tabs=[...document.querySelectorAll('[data-ai-tab]')];const i=tabs.indexOf(button);const next=tabs[(i+(['ArrowRight','ArrowDown'].includes(e.key)?1:-1)+tabs.length)%tabs.length];next.click();next.focus();}});
  }
  form.addEventListener('submit',async event=>{
    if(area==='history'){event.preventDefault();event.stopImmediatePropagation();return;}
    if(area==='paths')return;event.preventDefault();event.stopImmediatePropagation();if(!draft||busy)return;
    const savedArea=area, payload={config:structuredClone(draft),api_key:key,check_id:crypto.randomUUID()};
    const task={controller:new AbortController(),checkId:payload.check_id,cancelled:false,pending:true,phase:'checking',config:payload.config,message:'연결과 모델 기능을 검증하고 있습니다.'};
    saves.set(savedArea,task);render();
    const refresh=()=>{if(saves.get(savedArea)===task&&area===savedArea&&!dialog.hidden)render();};
    try {
      await api('/api/ai-settings/'+savedArea+'/check','POST',payload,task.controller.signal);
      if(task.cancelled)return;
      task.phase='saving';task.message='검증을 완료했습니다. 저장하고 있습니다.';refresh();
      const saved=await api('/api/ai-settings/'+savedArea,'PUT',payload);
      // Other tabs may have their own pending save or unsaved edits.
      if(response)response.settings[savedArea]=saved.settings[savedArea];else response=saved;
      task.message='저장했습니다. 재시작하면 적용됩니다.';
      if(area===savedArea){key='';draft=structuredClone(saved.settings[savedArea]);}
      document.dispatchEvent(new Event('settings-saved'));
    } catch(e) {
      if(task.cancelled)return;
      task.message=(e.name==='TimeoutError'?'연결 검증 시간이 초과되었습니다.':e.message)+' 설정은 저장하지 않았습니다.';
    } finally {
      task.pending=false;task.phase='done';payload.api_key='';refresh();
    }
  },true);
  async function loadHistory() {
    const own=++historyGeneration, list=$('#approvalHistoryList'), status=$('#approvalHistoryStatus');
    status.textContent='불러오는 중…'; list.replaceChildren();
    $('#historyPrevious').disabled=$('#historyNext').disabled=true;
    try {
      const data=await api(`/api/suggestion-history?offset=${historyOffset}&limit=25`);
      if(own!==historyGeneration || area!=='history')return;
      status.textContent=data.total?`승인 ${data.total}건`:'아직 저장된 승인 이력이 없습니다.';
      for(const item of data.items) {
        const row=element('details','approval-entry'), summary=element('summary');
        const title=element('div','approval-change');
        title.append(element('del','',item.original),element('span','approval-arrow','→'),element('strong','',item.replacement));
        const time=value=>Number.isFinite(value)?`${Math.floor(value/60)}:${String(Math.floor(value%60)).padStart(2,'0')}`:'미기록';
        summary.append(title,element('small','',`${item.recording_title} · ${time(item.start)}–${time(item.end)}`));
        const body=element('div','approval-evidence');
        for(const [label,value] of [['수정 전 발화',item.context_before],['수정 후 발화',item.context_after],['제안 이유',item.reason],['승인 시각',new Date(item.approved_at).toLocaleString()],['검수 모델',item.model||'미기록']]) {
          const group=element('div');group.append(element('h4','',label),element('p','',value));body.append(group);
        }
        row.append(summary,body);list.append(row);
      }
      $('#historyPage').textContent=data.total?`${Math.floor(historyOffset/25)+1} / ${Math.ceil(data.total/25)}`:'';
      $('#historyPrevious').disabled=historyOffset===0;
      $('#historyNext').disabled=historyOffset+25>=data.total;
    }catch(e){if(own===historyGeneration)status.textContent=e.message;}
  }
  $('#historyPrevious').addEventListener('click',()=>{historyOffset=Math.max(0,historyOffset-25);void loadHistory();});
  $('#historyNext').addEventListener('click',()=>{historyOffset+=25;void loadHistory();});
  async function enterSettings(resetArea=true) {
    const own=++generation;
    message.textContent='';
    if(resetArea)area='paths';
    checking=false;
    loading=true;draft=null;key='';render();
    try {
      const result=await Promise.all([api('/api/ai-settings'),api('/api/capabilities')]);
      if(own!==generation)return;
      [response,capabilities]=result;
      draft=['speech','llm'].includes(area)?structuredClone(saves.get(area)?.pending?saves.get(area).config:response.settings[area]):null;
    }catch(e){if(own===generation)message.textContent=e.message;}
    finally{if(own===generation){loading=false;render();}}
  }
  dialog.addEventListener('settings-reset',async()=>{
    const cancellations=[];
    for(const [savedArea,task] of saves) {
      if(task.pending&&task.phase==='checking') {
        task.cancelled=true;task.controller.abort();
        cancellations.push(api('/api/ai-settings/'+savedArea+'/check/cancel','POST',{check_id:task.checkId}).catch(()=>{}));
        saves.delete(savedArea);
      } else if(!task.pending) saves.delete(savedArea);
    }
    await enterSettings(false);
    message.textContent='변경과 진행 중인 검증을 취소했습니다.';
    await Promise.all(cancellations);
  });
  new MutationObserver(()=>{
    if(!dialog.hidden)void enterSettings();
    else{generation++;historyGeneration++;loading=false;busy=false;key='';draft=null;panels.replaceChildren();}
  }).observe(dialog,{attributes:true,attributeFilter:['hidden']});
}
