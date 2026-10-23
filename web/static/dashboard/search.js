import {requestJson} from './api.js';
import {openNote} from '../notes.js';
import {node,picker} from './components.js';

export function createSearch({app,$,actions,listen}) {
  const input=$('#globalSearch'),panel=$('#globalSearchResults'),matches=$('#searchMatches');
  let generation=0,timer,course='',kind='',offset=0;
  function filters() {
    const filters = $('#searchFilters'); filters.replaceChildren();
    picker(filters,'강의 필터',[['','모든 강의'],['0','선택 안 함'],...app.courses.map(c=>[String(c.id),c.name])],course,value=>{course=value;filtersRefresh();});
    picker(filters,'자료 종류',[['','모든 자료'],['recording','녹음·변환 텍스트'],['note','강의노트']],kind,value=>{kind=value;filtersRefresh();});
  }
  function filtersRefresh() {filters(); void search();}
  function sizeSearchPanel() {
    const available=Math.max(160,window.innerHeight-input.getBoundingClientRect().bottom-24);
    panel.style.setProperty('--search-available',`${available}px`);
  }
  listen(window,'resize',sizeSearchPanel);
  async function search(more = false) {
    const query=input.value.trim(), version=++generation;
    if (!more) {offset=0;matches.replaceChildren();}
    if (!query) {panel.hidden=true;return;}
    sizeSearchPanel();panel.hidden=false;
    panel.setAttribute('aria-busy','true');
    $('#searchEmpty').hidden=true;$('#searchMore').hidden=true;
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
      $('#searchEmpty').hidden=data.total!==0 || !!data.error || !!data.indexing;
      offset+=data.results.length;
      $('#searchMore').hidden=offset>=data.total;
      if(data.indexing) {clearTimeout(timer);timer=setTimeout(()=>{if(!panel.hidden&&input.value.trim()===query)void search();},1200);}
    } catch(error) {if(version===generation)$('#searchSummary').textContent=error.message;}
    finally {if(version===generation)panel.setAttribute('aria-busy','false');}
  }
  listen(input,'input',()=>{clearTimeout(timer);generation++;timer=setTimeout(()=>void search(),180);});
  listen(input,'focus',()=>{filters();if(input.value.trim())void search();});
  listen($('#searchMore'),'click',()=>void search(true));
  listen($('#globalSearchBox'),'keydown',event=>{
    if(event.key==='Escape'){generation++;clearTimeout(timer);input.focus();panel.hidden=true;return;}
    if(['ArrowDown','ArrowUp'].includes(event.key)){
      const buttons=[...matches.querySelectorAll('button')];if(!buttons.length)return;
      event.preventDefault();const index=buttons.indexOf(document.activeElement);
      const next=buttons[Math.max(0,Math.min(buttons.length-1,index+(event.key==='ArrowDown'?1:-1)))];
      next.focus();next.scrollIntoView({block:'nearest'});
    }
  });
  listen(document,'click',event=>{
    if(!$('#globalSearchBox').contains(event.target))panel.hidden=true;
  });
}
