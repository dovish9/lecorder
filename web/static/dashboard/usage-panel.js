import {requestJson} from './api.js';
import {usageSummary,usageChart,compactNumber,createUsagePeriod} from './usage.js';
import {node,picker,duration,number} from './components.js';

export function createUsagePanel({app,$,listen,every}) {
  let days=1,usage=null,loading=false,usageFingerprint='';
  const periods=[[1,'오늘'],[7,'7일'],[30,'30일'],[0,'전체']];
  const fetchUsage = (days,signal) => requestJson('/api/usage?'+new URLSearchParams({days:String(days),timezone_offset:String(-new Date().getTimezoneOffset()),timezone:Intl.DateTimeFormat().resolvedOptions().timeZone}),{signal});
  function period() {
    const root=$('#usagePeriod');root.replaceChildren();
    picker(root,'사용량 기간',periods,days,value=>{days=value;period();void refresh();});
  }
  const metric = (value,kind) => kind==='llm' ? `${compactNumber(value)}${value==null?'':' 토큰'}` : duration(value);
  function showDetails() {
    if(!usage)return;
    const dialog=node('dialog','','insight-dialog usage-dialog');
    dialog.setAttribute('aria-labelledby','usageDialogTitle');
    const header=node('header',''), close=node('button','닫기');close.type='button';close.addEventListener('click',()=>dialog.close());
    const title=node('h2','AI 사용량');title.id='usageDialogTitle';header.append(title,close);dialog.append(header);
    dialog.append(node('p','재시도를 포함한 요청 기준 · 각 항목의 기간을 따로 선택할 수 있습니다.', 'insight-caption'));
    const loaders=[];
    const columns=node('div','','usage-columns');
    for(const [kind,label] of [['speech','음성 변환'],['llm','LLM']]){
      const section=node('section','',`usage-column usage-${kind}`);
      const toolbar=node('div','','usage-column-toolbar'), control=node('div','');
      toolbar.append(node('h3',label),control);
      const content=node('div','','usage-column-body');section.append(toolbar,content);columns.append(section);
      const loader=createUsagePeriod(fetchUsage,({days,loading,data,error})=>{
        if(control.dataset.days!==String(days)){
          const focused=control.contains(document.activeElement);
          control.dataset.days=String(days);control.replaceChildren();
          picker(control,`${label} 상세 기간`,periods,days,value=>loader.select(value));
          if(focused)control.querySelector('summary').focus();
        }
        content.replaceChildren();content.setAttribute('aria-busy',String(loading));
        if(loading){content.append(node('p','사용량을 불러오는 중…','insight-caption usage-column-status'));return;}
        if(error){
          const message=node('p',error.message,'insight-caption');message.setAttribute('role','alert');
          const retry=node('button','다시 시도');retry.type='button';retry.addEventListener('click',()=>loader.select(days));
          const status=node('div','','usage-column-status');status.append(message,retry);content.append(status);return;
        }
        renderUsage(content,data,kind,days);
      });
      loaders.push(loader);loader.select(7);
    }
    function renderUsage(section,usage,kind,days){
      const summary=usageSummary(usage.groups,kind);
      const overview=node('div','','usage-overview');
      overview.append(node('p',usage.start_date&&usage.end_date?`${usage.start_date} — ${usage.end_date}`:periods.find(([value])=>value===days)[1],'insight-caption usage-date-range'));
      const headline=node('div','','usage-headline');headline.append(node('strong',metric(summary.value,kind)),node('small',`${number(summary.requests)}회`));overview.append(headline);
      overview.append(node('p',kind==='speech'?'최종 음성 변환과 실시간 초안의 처리 음성 시간':'제공된 입력·출력 토큰 합계','insight-caption'));
      section.append(overview);
      const chart=node('div','','usage-chart-block');chart.append(usageChart(usage,kind));
      chart.append(node('p','■ 일별 사용량   ┄ 기간 내 누적 · 좌/우 축 단위 동일, 눈금 별도','usage-legend'));section.append(chart);
      const stats=node('dl','','usage-stats');
      const values=[['처리 시간',duration(summary.seconds)],['완료 / 취소 / 실패',`${summary.completed} / ${summary.cancelled} / ${summary.failed}`],['사용량 미제공',`${summary.unknown}회`],['처리 시간 미제공',`${summary.unknownTime}회`]];
      for(const [location,label] of [['local','로컬 요청'],['external','외부 API 요청'],['custom','사용자 서버 요청']])values.push([label,`${summary.rows.filter(r=>r.location===location).reduce((n,r)=>n+r.requests,0)}회`]);
      if(kind==='llm')values.unshift(['입력 토큰',compactNumber(summary.input)],['출력 토큰',compactNumber(summary.output)]);
      for(const [name,value] of values)stats.append(node('dt',name),node('dd',value));section.append(stats);
      const models=node('div','','usage-models-block');models.append(node('h4','모델별 요청'));
      for(const group of summary.rows){
        const block=node('div','','usage-model');
        block.append(node('strong',group.model),node('small',`${{local:'로컬',external:'외부 API',custom:'사용자 서버'}[group.location]||group.location} · ${group.provider}${group.kind==='live_speech'?' · 실시간 초안':''}`),node('span',`${number(group.requests)}회 · ${metric(usageSummary([group],kind).value,kind)}`));models.append(block);
      }
      if(!summary.rows.length)models.append(node('p','이 기간의 요청 기록이 없습니다.','insight-caption'));
      section.append(models);
      const history=node('div','','usage-history-block');
      if(usage.recording_since)history.append(node('p',`요청별 기록 ${new Date(usage.recording_since).toLocaleDateString('ko-KR')}부터${usage.partial_history?' · 과거 기록 일부 포함':''}`,'insight-caption'));
      section.append(history);
    }
    dialog.append(columns,node('p','취소된 요청의 사용량은 제공되지 않을 수 있습니다. 미제공 수치는 그래프와 합계에서 제외합니다. 음성 시간에는 반복 처리한 구간이 포함됩니다.','insight-caption'));
    dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
    dialog.addEventListener('close',()=>{loaders.forEach(loader=>loader.close());dialog.remove();});document.body.append(dialog);dialog.showModal();
  }
  async function refresh(){
    if(app.activeView!=='dashboard'||loading)return;
    loading=true;
    try{
      const data=await fetchUsage(days);
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
    }catch(error){$('#usageError').textContent=error.message;$('#usageError').hidden=false;}finally{loading=false;}
  }
  every(()=>void refresh(),5000);listen(window,'hashchange',()=>void refresh());period();void refresh();
  return {refresh};
}
