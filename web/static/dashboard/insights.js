import {requestJson} from './api.js';
import {openNote} from '../notes.js';

const node = (tag, text, className = '') => Object.assign(document.createElement(tag), {textContent:text, className});
const duration = seconds => {
  if(seconds==null)return '미제공';
  const value=Math.round(seconds);
  return value<60?`${value}초`:`${Math.floor(value/60)}분 ${value%60}초`;
};
const number = value => value == null ? '미제공' : Number(value).toLocaleString('ko-KR');

export function createInsights({app, $, actions, listen, every}) {
  const input = $('#globalSearch'), panel = $('#globalSearchResults'), matches = $('#searchMatches');
  let generation = 0, timer, course = '', kind = '', offset = 0, days = 1, usage = null, transfer = null;
  let loading = false;
  let recoveryFingerprint='';
  function picker(parent, label, options, selected, change) {
    const detail = node('details', '', 'insight-picker');
    const summary = node('summary', options.find(([value]) => value === selected)?.[1] || label);
    summary.setAttribute('aria-label', label);
    const list = node('div', '', 'insight-options');
    for (const [value, title] of options) {
      const button = node('button', title); button.type = 'button';
      button.setAttribute('aria-pressed', String(value === selected));
      button.addEventListener('click', () => {detail.open = false; change(value);});
      list.append(button);
    }
    detail.append(summary,list); parent.append(detail);
    detail.addEventListener('keydown', event => {if (event.key === 'Escape') {detail.open=false;summary.focus();}});
  }
  function filters() {
    const filters = $('#searchFilters'); filters.replaceChildren();
    picker(filters,'강의 필터',[['','모든 강의'],['0','선택 안 함'],...app.courses.map(c=>[String(c.id),c.name])],course,value=>{course=value;filtersRefresh();});
    picker(filters,'자료 종류',[['','모든 자료'],['recording','녹음·전사문'],['note','강의노트']],kind,value=>{kind=value;filtersRefresh();});
  }
  function filtersRefresh() {filters(); void search();}
  async function search(more = false) {
    const query=input.value.trim(), version=++generation;
    if (!more) {offset=0;matches.replaceChildren();}
    if (!query) {panel.hidden=true;return;}
    panel.hidden=false;
    $('#searchSummary').textContent='검색 중…';
    try {
      const data=await requestJson('/api/search?'+new URLSearchParams({q:query,course,kind,offset:String(offset)}));
      if(version!==generation)return;
      $('#searchSummary').textContent=data.error || `${data.total}개 결과${data.indexing?' · 검색 자료 준비 중':''}`;
      for(const result of data.results){
        const button=node('button','','search-match');button.type='button';
        const location=result.page ? `${result.page}쪽` : result.seconds!=null ? `${Math.floor(result.seconds/60)}:${String(Math.floor(result.seconds%60)).padStart(2,'0')}` : '';
        button.append(node('strong',result.title),node('small',`${result.course} · ${result.kind==='note'?'강의노트':'녹음'} ${location}`),node('span',result.snippet));
        button.addEventListener('click',async()=>{
          try {
          panel.hidden=true;
          if(result.kind==='note') {await openNote(result.target,{tab:result.tab,page:result.page});return;}
          if(!app.recordings.some(row=>row.id===result.target)) {
            const data=await requestJson(`/api/recordings/${encodeURIComponent(result.target)}`);
            actions.renderRecordings([...app.recordings,data.recording]);
          }
          await actions.refreshStorage();
          window.location.hash=`#/recordings/job/${encodeURIComponent(result.target)}?time=${result.seconds??0}`;
          } catch(error) {actions.showToast(error.message,'error');}
        });
        matches.append(button);
      }
      offset+=data.results.length;
      $('#searchMore').hidden=offset>=data.total;
      if(data.indexing) {clearTimeout(timer);timer=setTimeout(()=>{if(!panel.hidden&&input.value.trim()===query)void search();},1200);}
    } catch(error) {if(version===generation)$('#searchSummary').textContent=error.message;}
  }
  listen(input,'input',()=>{clearTimeout(timer);generation++;timer=setTimeout(()=>void search(),180);});
  listen(input,'focus',()=>{filters();if(input.value.trim())void search();});
  listen($('#searchMore'),'click',()=>void search(true));
  listen($('#globalSearchBox'),'keydown',event=>{
    if(event.key==='Escape'){generation++;clearTimeout(timer);input.focus();panel.hidden=true;return;}
    if(['ArrowDown','ArrowUp'].includes(event.key)){
      const buttons=[...matches.querySelectorAll('button')];if(!buttons.length)return;
      event.preventDefault();const index=buttons.indexOf(document.activeElement);
      buttons[Math.max(0,Math.min(buttons.length-1,index+(event.key==='ArrowDown'?1:-1)))].focus();
    }
  });
  listen(document,'click',event=>{
    if(!$('#globalSearchBox').contains(event.target))panel.hidden=true;
    document.querySelectorAll('.insight-picker[open]').forEach(d=>{if(!d.contains(event.target))d.open=false;});
  });
  function period() {
    const root=$('#usagePeriod');root.replaceChildren();
    picker(root,'사용량 기간',[[1,'오늘'],[7,'7일'],[30,'30일'],[0,'전체']],days,value=>{days=value;period();void refresh();});
  }
  function showDetails() {
    const dialog=node('dialog','','insight-dialog');
    const header=node('header','');const close=node('button','닫기');close.addEventListener('click',()=>dialog.close());
    header.append(node('h2','모델별 사용량'),close);dialog.append(header);
    for(const group of usage?.groups || []){
      const block=node('section','','usage-detail');
      block.append(node('h3',`${group.provider} · ${group.model}`),node('p',`${group.kind==='live_speech'?'실시간 받아쓰기':group.kind==='speech'?'최종 음성 전사':'LLM'} · 요청 ${number(group.requests)}회 · 처리 ${duration(group.seconds)}`),
        node('p',group.kind==='llm'?`입력 ${number(group.input_tokens)} / 출력 ${number(group.output_tokens)} 토큰 · 미제공 ${number(group.unknown_usage)}회`:`처리한 음성 ${duration(group.audio_seconds)} (재시도 포함)`),node('small',`완료 ${group.completed} · 취소 ${group.cancelled} · 실패/중단 ${group.failed}`));
      dialog.append(block);
    }
    if(!usage?.groups.length)dialog.append(node('p','이 기간의 요청 기록이 없습니다.'));
    dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
    dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);dialog.showModal();
  }
  listen($('#usageDetails'),'click',showDetails);
  async function refresh(){
    if(app.activeView!=='dashboard'||loading)return;
    loading=true;
    try{
      const [data,recovery]=await Promise.all([requestJson('/api/usage?'+new URLSearchParams({days:String(days),timezone_offset:String(-new Date().getTimezoneOffset()),timezone:Intl.DateTimeFormat().resolvedOptions().timeZone})),requestJson('/api/recovery')]);
      usage=data;const groups=$('#usageGroups');groups.replaceChildren();
      for(const [key,label] of [['local','로컬'],['external','외부 API'],['custom','사용자 서버']]){
        const rows=data.groups.filter(r=>r.location===key);if(key==='custom'&&!rows.length)continue;
        const block=node('div','','usage-summary');const sum=field=>rows.reduce((total,row)=>total+(row[field]||0),0);
        block.append(node('strong',label),node('span',`${number(sum('requests'))}회`),node('small',`처리 ${duration(sum('seconds'))}${sum('unknown_time')?' · 시간 미제공 '+sum('unknown_time')+'회':''}`));groups.append(block);
      }
      $('#usageCaption').textContent=data.recording_since?`요청별 기록 ${new Date(data.recording_since).toLocaleDateString('ko-KR')}부터 · 재시도 포함`:'새 요청부터 사용량을 기록합니다.';
      if(data.partial_history) $('#usageCaption').textContent+=' · 과거 기록 일부 포함';
      const fingerprint=JSON.stringify(recovery.items);
      if(fingerprint!==recoveryFingerprint){
      recoveryFingerprint=fingerprint;
      const notices=$('#recoveryNotices');notices.replaceChildren();
      for(const item of recovery.items){
        const block=node('div','','recovery-notice');const ack=node('button','확인');ack.type='button';
        block.append(node('span',`${item.title || '작업'} · ${item.message}`),ack);ack.addEventListener('click',async()=>{try{await requestJson(`/api/recovery/${item.id}/acknowledge`,{method:'POST'});void refresh();}catch(error){actions.showToast(error.message,'error');}});notices.append(block);
      }
      }
    }catch(error){$('#usageCaption').textContent=error.message;}finally{loading=false;}
  }
  function transferView(job){
    const root=$('#backupProgress');root.replaceChildren(node('p',job.message||'자료를 준비하고 있습니다…'));
    if(job.state==='running'){
      const cancel=node('button','취소');cancel.addEventListener('click',()=>requestJson(`/api/backups/${job.id}/cancel`,{method:'POST'}));root.append(cancel);
    }else if(job.state==='completed'&&job.operation==='export'){
      const download=node('a','백업 다운로드');download.href=`/api/backups/${job.id}/download`;root.append(download);
    }else if(job.state==='ready'){
      root.append(node('p',`강의 ${job.summary.courses}개 · 녹음 ${job.summary.recordings}개 · 노트 ${job.summary.lecture_notes}개`));
      if(job.collisions?.length) root.append(node('p',`이름 충돌: ${job.collisions.join(', ')} · 번호를 붙여 추가합니다.`));
      const confirm=node('button','별도 사본으로 가져오기');confirm.addEventListener('click',()=>start(`/api/backups/${job.id}/restore`));root.append(confirm);
    }
  }
  async function start(url,body){
    try{transfer=await requestJson(url,{method:'POST',...(body?{body}: {})});sessionStorage.setItem('lecorder-transfer',transfer.id);transferView(transfer);}
    catch(error){$('#backupProgress').textContent=error.message;}
  }
  listen($('#backupCreate'),'click',()=>start('/api/backups'));
  listen($('#backupImport'),'click',()=>$('#backupFile').click());
  listen($('#backupFile'),'change',()=>{const file=$('#backupFile').files[0];if(!file)return;const body=new FormData();body.append('file',file);void start('/api/backups/inspect',body);$('#backupFile').value='';});
  every(async()=>{
    if(transfer?.state==='running'){
      try{transfer=await requestJson(`/api/backups/${transfer.id}`);transferView(transfer);if(transfer.state==='completed'){
        if(transfer.operation==='restore'){
          const data=await requestJson('/api/bootstrap');app.courses=data.courses;actions.renderCourseList();
        }
        await actions.refreshStatus();await actions.refreshStorage();void refresh();
      }}
      catch(error){$('#backupProgress').textContent=error.message;}
    }
  },1500);
  const previousTransfer=sessionStorage.getItem('lecorder-transfer');
  if(previousTransfer)requestJson(`/api/backups/${encodeURIComponent(previousTransfer)}`).then(job=>{transfer=job;transferView(job);}).catch(()=>sessionStorage.removeItem('lecorder-transfer'));
  every(()=>void refresh(),5000);listen(window,'hashchange',()=>void refresh());period();void refresh();
  return {refreshInsights:refresh};
}
