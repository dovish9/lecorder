import {requestJson} from './api.js';
import {usageSummary, usageChart, compactNumber} from './usage.js';
import {openNote} from '../notes.js';

const node = (tag, text, className = '') => Object.assign(document.createElement(tag), {textContent:text, className});
const duration = seconds => {
  if(seconds==null)return '미제공';
  const value=Math.round(seconds);
  if(value>=3600)return `${Math.floor(value/3600)}시간 ${Math.floor(value%3600/60)}분`;
  return value<60?`${value}초`:`${Math.floor(value/60)}분 ${value%60}초`;
};
const number = value => value == null ? '미제공' : Number(value).toLocaleString('ko-KR');

export function createInsights({app, $, actions, listen, every}) {
  const input = $('#globalSearch'), panel = $('#globalSearchResults'), matches = $('#searchMatches');
  let generation = 0, timer, course = '', kind = '', offset = 0, days = 1, usage = null, transfer = null;
  let loading = false;
  let recoveryFingerprint='', usageFingerprint='';
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
  const metric = (value,kind) => kind==='llm' ? `${compactNumber(value)}${value==null?'':' 토큰'}` : duration(value);
  function showDetails() {
    if(!usage)return;
    const dialog=node('dialog','','insight-dialog usage-dialog');
    dialog.setAttribute('aria-labelledby','usageDialogTitle');
    const header=node('header',''), close=node('button','닫기');close.type='button';close.addEventListener('click',()=>dialog.close());
    const title=node('h2','AI 사용량');title.id='usageDialogTitle';header.append(title,close);dialog.append(header);
    dialog.append(node('p',`${usage.start_date&&usage.end_date?usage.start_date+' — '+usage.end_date:({1:'오늘',7:'7일',30:'30일',0:'전체'}[days])} · 재시도를 포함한 요청 기준`, 'insight-caption'));
    const columns=node('div','','usage-columns');
    for(const [kind,label] of [['speech','음성 변환'],['llm','LLM']]){
      const summary=usageSummary(usage.groups,kind), section=node('section','',`usage-column usage-${kind}`);
      section.append(node('h3',label));
      const headline=node('div','','usage-headline');headline.append(node('strong',metric(summary.value,kind)),node('small',`${number(summary.requests)}회`));section.append(headline);
      section.append(node('p',kind==='speech'?'최종 전사와 실시간 초안의 처리 음성 시간':'제공된 입력·출력 토큰 합계','insight-caption'));
      section.append(usageChart(usage,kind));
      section.append(node('p','■ 일별 사용량   ┄ 기간 내 누적 · 좌/우 축 단위 동일, 눈금 별도','usage-legend'));
      const stats=node('dl','','usage-stats');
      const values=[['처리 시간',duration(summary.seconds)],['완료 / 취소 / 실패',`${summary.completed} / ${summary.cancelled} / ${summary.failed}`],['사용량 미제공',`${summary.unknown}회`],['처리 시간 미제공',`${summary.unknownTime}회`]];
      for(const [location,label] of [['local','로컬 요청'],['external','외부 API 요청'],['custom','사용자 서버 요청']])values.push([label,`${summary.rows.filter(r=>r.location===location).reduce((n,r)=>n+r.requests,0)}회`]);
      if(kind==='llm')values.unshift(['입력 토큰',compactNumber(summary.input)],['출력 토큰',compactNumber(summary.output)]);
      for(const [name,value] of values)stats.append(node('dt',name),node('dd',value));section.append(stats);
      section.append(node('h4','모델별 요청'));
      for(const group of summary.rows){
        const block=node('div','','usage-model');
        block.append(node('strong',group.model),node('small',`${{local:'로컬',external:'외부 API',custom:'사용자 서버'}[group.location]||group.location} · ${group.provider}${group.kind==='live_speech'?' · 실시간 초안':''}`),node('span',`${number(group.requests)}회 · ${metric(usageSummary([group],kind).value,kind)}`));section.append(block);
      }
      if(!summary.rows.length)section.append(node('p','이 기간의 요청 기록이 없습니다.','insight-caption'));
      columns.append(section);
    }
    dialog.append(columns,node('p','취소된 요청의 사용량은 제공되지 않을 수 있습니다. 미제공 수치는 그래프와 합계에서 제외합니다. 음성 시간에는 반복 처리한 구간이 포함됩니다.','insight-caption'));
    if(usage.recording_since)dialog.append(node('p',`요청별 기록 ${new Date(usage.recording_since).toLocaleDateString('ko-KR')}부터${usage.partial_history?' · 과거 기록 일부 포함':''}`,'insight-caption'));
    dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
    dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);dialog.showModal();
  }
  async function refresh(){
    if(app.activeView!=='dashboard'||loading)return;
    loading=true;
    try{
      const [data,recovery]=await Promise.all([requestJson('/api/usage?'+new URLSearchParams({days:String(days),timezone_offset:String(-new Date().getTimezoneOffset()),timezone:Intl.DateTimeFormat().resolvedOptions().timeZone})),requestJson('/api/recovery')]);
      usage=data;const groups=$('#usageGroups');
      if(usageFingerprint!==JSON.stringify(data.groups)){
      usageFingerprint=JSON.stringify(data.groups);groups.replaceChildren();
      for(const [kind,label] of [['speech','음성 변환'],['llm','LLM']]){
        const summary=usageSummary(data.groups,kind), block=node('button','',`usage-summary usage-${kind}`);block.type='button';
        block.setAttribute('aria-label',`${label} 사용량 상세 보기`);block.setAttribute('aria-haspopup','dialog');
        const headline=node('div','','usage-headline');headline.append(node('span',metric(summary.value,kind)),node('small',`${number(summary.requests)}회`));
        block.append(node('strong',label),headline,node('small',kind==='speech'?'처리한 음성 시간':'입력 + 출력 토큰'));
        if(summary.unknown)block.append(node('small',`일부 미제공 · ${summary.unknown}회`));
        block.addEventListener('click',showDetails);groups.append(block);
      }
      }
      $('#usageError').hidden=true;
      const fingerprint=JSON.stringify(recovery.items);
      if(fingerprint!==recoveryFingerprint){
      recoveryFingerprint=fingerprint;
      const notices=$('#recoveryNotices');notices.replaceChildren();
      $('#recoveryCount').textContent=recovery.items.length;
      if(!recovery.items.length)notices.append(node('p','확인할 복구 내역이 없습니다.','empty-state'));
      for(const item of recovery.items){
        const block=node('div','','recovery-notice');const ack=node('button','확인');ack.type='button';
        block.append(node('span',`${item.title || '작업'} · ${item.message}`),ack);ack.addEventListener('click',async()=>{try{await requestJson(`/api/recovery/${item.id}/acknowledge`,{method:'POST'});void refresh();}catch(error){actions.showToast(error.message,'error');}});notices.append(block);
      }
      }
    }catch(error){$('#usageError').textContent=error.message;$('#usageError').hidden=false;}finally{loading=false;}
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
