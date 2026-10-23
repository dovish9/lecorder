import {requestJson} from './api.js';
import {node} from './components.js';

export function createRecovery({app,$,actions,listen,every}) {
  let loading=false,recoveryFingerprint='';
  async function refresh(){
    if(app.activeView!=='dashboard'||loading)return;
    loading=true;
    try {
      const recovery=await requestJson('/api/recovery');
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
      $('#recoveryError').hidden=true;
    } catch(error){$('#recoveryError').textContent=error.message;$('#recoveryError').hidden=false;}
    finally{loading=false;}
  }
  every(()=>void refresh(),5000);listen(window,'hashchange',()=>void refresh());void refresh();
  return {refresh};
}
