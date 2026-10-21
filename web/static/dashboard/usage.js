export const compactNumber = value => value == null ? '미제공' : new Intl.NumberFormat('en-US', {notation:'compact',maximumFractionDigits:1}).format(value);
const sumKnown = values => values.some(v => v != null) ? values.reduce((a,v)=>a+(v??0),0) : null;
export function usageSummary(groups, kind) {
  const rows=groups.filter(r=>kind==='llm'?r.kind==='llm':r.kind!=='llm');
  const sum=field=>rows.reduce((a,r)=>a+(r[field]??0),0);
  return {rows, requests:sum('requests'), completed:sum('completed'), cancelled:sum('cancelled'), failed:sum('failed'),
    seconds:rows.length?sumKnown(rows.map(r=>r.seconds)):0,
    input:rows.length?sumKnown(rows.map(r=>r.input_tokens)):0, output:rows.length?sumKnown(rows.map(r=>r.output_tokens)):0,
    value:rows.length?sumKnown(rows.flatMap(r=>kind==='llm'?[r.input_tokens,r.output_tokens]:[r.audio_seconds])):0,
    unknown:sum(kind==='llm'?'unknown_usage':'unknown_audio'), unknownTime:sum('unknown_time')};
}
export function usageSeries(data, kind) {
  if(!Array.isArray(data.daily)||!data.start_date||!data.end_date)return [];
  const rows=new Map((data.daily||[]).filter(r=>r.kind===kind).map(r=>[r.date,r]));
  const start=new Date(`${data.start_date}T00:00:00Z`), end=new Date(`${data.end_date}T00:00:00Z`);
  let cumulative=0;const points=[];
  for(let date=start;date<=end;date=new Date(date.getTime()+86400000)){
    const key=date.toISOString().slice(0,10), row=rows.get(key);
    const value=row?row.value:0;cumulative+=value??0;
    points.push({date:key,value,cumulative,unknown:row?.unknown||0,requests:row?.requests||0});
  }
  return points;
}
export function usageChart(data,kind) {
  const points=usageSeries(data,kind), svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  const unit=kind==='llm'?'토큰':'분', divisor=kind==='llm'?1:60;
  svg.setAttribute('viewBox','0 0 440 210');svg.setAttribute('role','img');
  svg.setAttribute('aria-label',`일별 ${unit} 막대와 기간 내 누적 ${unit} 점선 그래프. 미제공 사용량은 제외됩니다.`);
  const add=(tag,attrs,text)=>{const e=document.createElementNS(svg.namespaceURI,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);if(text)e.textContent=text;svg.append(e);return e;};
  if(!points.length){
    svg.setAttribute('aria-label','일별 그래프는 서버를 다시 시작한 후 제공됩니다.');
    add('text',{x:220,y:100,'text-anchor':'middle'},'일별 그래프는 서버를 다시 시작한 후 제공됩니다.');
    return svg;
  }
  const top=28,bottom=170,left=48,width=344;
  const dailyMax=Math.max(1,...points.map(p=>(p.value??0)/divisor)),totalMax=Math.max(1,...points.map(p=>p.cumulative/divisor));
  const label=compactNumber;
  add('text',{x:left,y:12,'text-anchor':'start'},`일별 (${unit})`);add('text',{x:left+width,y:12,'text-anchor':'end'},`누적 (${unit})`);
  for(let i=0;i<=2;i++){const y=bottom-(bottom-top)*i/2;add('line',{x1:left,x2:left+width,y1:y,y2:y,class:'usage-gridline'});add('text',{x:left-7,y:y+4,'text-anchor':'end'},label(dailyMax*i/2));add('text',{x:left+width+7,y:y+4},label(totalMax*i/2));}
  const step=width/Math.max(points.length,1),x=i=>left+step*(i+.5);
  points.forEach((p,i)=>{const h=(p.value??0)/divisor/dailyMax*(bottom-top);const bar=add('rect',{x:x(i)-Math.max(.5,step*.3),y:bottom-h,width:Math.max(1,step*.6),height:h,rx:Math.min(3,step*.1),class:'usage-bar'});const title=document.createElementNS(svg.namespaceURI,'title');title.textContent=`${p.date}: ${p.value==null?'미제공':label(p.value/divisor)+' '+unit} · ${p.requests}회${p.unknown?' · 일부 미제공':''}`;bar.append(title);});
  add('polyline',{points:points.map((p,i)=>`${x(i)},${bottom-p.cumulative/divisor/totalMax*(bottom-top)}`).join(' '),class:'usage-cumulative'});
  if(points.length===1)add('circle',{cx:x(0),cy:bottom-points[0].cumulative/divisor/totalMax*(bottom-top),r:3,class:'usage-dot'});
  [...new Set([0,Math.floor((points.length-1)/2),points.length-1])].filter(i=>i>=0).forEach(i=>add('text',{x:x(i),y:195,'text-anchor':'middle'},points[i].date.slice(5).replace('-','/')));
  return svg;
}
