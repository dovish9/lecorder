import {requestJson} from './api.js';
import {node} from './components.js';

export function createTransfers({app,$,actions,listen,every},refreshSummary) {
  let transfer=null;
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
    try{transfer=await requestJson(url,{method:'POST',...(body?{body}: {})});sessionStorage.setItem(`lecorder-transfer:${app.datasetId}`,transfer.id);transferView(transfer);}
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
        await actions.refreshStatus();await actions.refreshStorage();void refreshSummary();
      }}
      catch(error){$('#backupProgress').textContent=error.message;}
    }
  },1500);
  let restoredDataset=null;
  every(()=>{
    if(!app.datasetId||restoredDataset===app.datasetId)return;
    restoredDataset=app.datasetId;
    const key=`lecorder-transfer:${app.datasetId}`, id=sessionStorage.getItem(key);
    if(id)requestJson(`/api/backups/${encodeURIComponent(id)}`).then(job=>{transfer=job;transferView(job);}).catch(()=>sessionStorage.removeItem(key));
  },1500);
}
