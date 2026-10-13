// Draft-only form state; credentials are never persisted in browser storage.
export function setupAISettings() {
  const $ = (s) => document.querySelector(s);
  const dialog = $('#systemDialog'), form = $('#systemForm');
  const panels = $('#aiSettingsPanel'), paths = $('#pathSettingsPanel');
  const message = $('#systemMessage'), save = $('#saveSystemButton'), hint = $('#systemSaveHint');
  let capabilities = {};
  let area = 'paths', response, draft, key = '', busy = false, checked = false, generation = 0;
  const providers = {speech: [['whisper_cpp','로컬 · whisper.cpp'],['openai','OpenAI']], llm: [['llama_cpp','로컬 · llama.cpp'],['openai','OpenAI'],['claude','Claude'],['gemini','Gemini']]};
  providers.speech[0][1] = '로컬 · whisper.cpp'; providers.llm[0][1] = '로컬 · llama.cpp';
  const presets = {
    openai: {base_url:'https://api.openai.com/v1', model:''},
    claude: {base_url:'https://api.anthropic.com/v1', model:''},
    gemini: {base_url:'https://generativelanguage.googleapis.com/v1beta', model:''},
  };
  const models = {openai:['gpt-4.1-mini','gpt-4.1'], claude:['claude-sonnet-4-5'], gemini:['gemini-2.5-flash','gemini-2.5-pro']};
  const element = (tag, cls, text) => {const e=document.createElement(tag); if(cls)e.className=cls; if(text!==undefined)e.textContent=text; return e;};
  async function api(url, method='GET', body) {
    const result = await fetch(url, {method, headers: {'Content-Type':'application/json'}, body: body===undefined ? undefined : JSON.stringify(body)});
    const data = await result.json(); if(!result.ok) throw new Error(data.error || '요청 실패'); return data;
  }
  function invalidate(){ checked=false; save.disabled=true; message.textContent='변경한 설정을 연결 확인한 뒤 저장하세요.'; }
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
    input.addEventListener('input',()=>{if(prop==='api_key')key=input.value;else draft[prop]=input.value;invalidate();});wrap.append(title,input);return wrap;
  }
  function render() {
    paths.hidden=area!=='paths'; panels.hidden=area==='paths';
    document.querySelectorAll('[data-ai-tab]').forEach(b=>{const active=b.dataset.aiTab===area;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;});
    hint.textContent=area==='paths'?'경로 저장 시 앱과 로컬 서버가 다시 시작됩니다.':'저장한 설정은 다음 작업부터 적용됩니다. 진행 중인 작업은 유지됩니다.';
    save.textContent=area==='paths'?'경로 저장':'설정 저장'; save.disabled=busy || (area!=='paths'&&!checked);
    panels.replaceChildren(); if(area==='paths'||!draft)return;
    const local=['llama_cpp','whisper_cpp'].includes(draft.provider);
    const top=element('div','ai-settings-intro');top.append(element('h3','',area==='speech'?'음성을 텍스트로':'검수와 강의노트 분석'),element('p','',area==='speech'?'언어와 강의노트 인식 힌트는 각 작업의 설정을 따릅니다.':'검수·개요·페이지 해설·의미 검증에 같은 모델을 사용합니다.'));panels.append(top);
    if(area==='speech')panels.append(element('p','ai-privacy','실시간 초안: 로컬 large-v3-turbo · 녹음 종료 후 전체 전사는 아래 제공자와 모델을 사용합니다.'));
    panels.append(picker('제공자',providers[area],draft.provider,id=>{
      draft=id===response.defaults[area].provider?structuredClone(response.defaults[area]):{provider:id,managed:false,...presets[id],model:area==='speech'?'whisper-1':''};key='';checked=false;render();
    }));
    if(local){const label=element('label','ai-managed');const check=element('input');check.disabled=busy;check.type='checkbox';check.checked=draft.managed;check.addEventListener('change',()=>{draft.managed=check.checked;checked=false;render();});label.append(check,element('span','','Lecorder가 서버 시작·종료 관리'));panels.append(label);}
    if(!local && area==='llm')panels.append(picker('모델 프리셋',[...(models[draft.provider]||[]).map(m=>[m,m]),['','직접 입력']],draft.model,id=>{draft.model=id;checked=false;render();}));
    panels.append(field('모델 ID','model'),field('서버 URL','base_url','url'));
    if(draft.managed){
      panels.append(field('서버 실행 파일','binary'),field('모델 파일','model_path'),field(area==='speech'?'VAD 모델 파일':'이미지 projector 파일',area==='speech'?'vad_path':'projector_path'));
    }else{
      panels.append(field('API 키','api_key','password'));
      const keyline=element('div','ai-key-line');keyline.append(element('span','',draft.key_present?'보안 저장소에 키가 저장되어 있습니다.':`키는 ${capabilities.credential_store || '운영체제 보안 저장소'}에 보관됩니다.`));
      const del=element('button','ai-text-button','저장된 키 삭제');del.disabled=busy;del.type='button';del.addEventListener('click',async()=>{if(!confirm('이 서버에 저장된 API 키를 삭제할까요?'))return;try{await api('/api/ai-settings/'+area+'/key','DELETE',{config:draft});draft.key_present=false;key='';checked=false;render();message.textContent='API 키를 삭제했습니다.';}catch(e){message.textContent=e.message;}});keyline.append(del);panels.append(keyline);
    }
    panels.append(element('p','ai-privacy',local&&draft.managed?'음성과 강의노트가 이 컴퓨터 안에서 처리됩니다.':`선택한 서버로 ${area==='speech'?'음성':'전사문과 강의노트 텍스트·이미지'}를 전송합니다. 외부 API 요금이 발생할 수 있으며, 요청 후 취소해도 이미 처리된 요청은 과금될 수 있습니다.`));
    const actions=element('div','ai-settings-actions'), reset=element('button','','기본값으로 초기화'),check=element('button','ai-check','연결 확인');reset.type=check.type='button';
    reset.disabled=check.disabled=busy;
    reset.addEventListener('click',()=>{draft=structuredClone(response.defaults[area]);key='';checked=false;render();message.textContent='로컬 기본값으로 되돌렸습니다. 연결 확인 후 저장하면 적용됩니다.';});
    check.addEventListener('click',async()=>{
      const own=++generation;busy=true;message.textContent='연결과 모델 기능을 확인하고 있습니다…';render();
      try{await api('/api/ai-settings/'+area+'/check','POST',{config:draft,api_key:key});if(own!==generation)return;checked=true;message.textContent=area==='llm'?'이미지 입력·구조화 응답 확인 완료':'전사 서버 연결 확인 완료';}
      catch(e){if(own===generation)message.textContent=e.message;}
      finally{if(own===generation){busy=false;render();}}
    });actions.append(reset,check);panels.append(actions);
  }
  for(const button of document.querySelectorAll('[data-ai-tab]')) {
    button.addEventListener('click',()=>{if(busy)return;area=button.dataset.aiTab;draft=area==='paths'?null:structuredClone(response?.settings[area]||{});key='';checked=false;message.textContent='';render();});
    button.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();const tabs=[...document.querySelectorAll('[data-ai-tab]')];const i=tabs.indexOf(button);const next=tabs[(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length];next.click();next.focus();}});
  }
  form.addEventListener('submit',async event=>{
    if(area==='paths')return;event.preventDefault();event.stopImmediatePropagation();if(!checked||busy)return;
    busy=true;render();
    try{response=await api('/api/ai-settings/'+area,'PUT',{config:draft,api_key:key});key='';draft=structuredClone(response.settings[area]);message.textContent='저장했습니다. 다음 작업부터 적용됩니다.';}
    catch(e){message.textContent=e.message;checked=false;}
    finally{busy=false;render();}
  },true);
  new MutationObserver(async()=>{if(dialog.open){area='paths';checked=false;render();try{[response, capabilities]=await Promise.all([api('/api/ai-settings'),api('/api/capabilities')]);}catch(e){message.textContent=e.message;}}else{generation++;busy=false;key='';draft=null;panels.replaceChildren();}}).observe(dialog,{attributes:true,attributeFilter:['open']});
}
