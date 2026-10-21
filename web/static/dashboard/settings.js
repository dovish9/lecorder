import {requestJson} from "./api.js";

export function createSettings(context) {
  const {app, ui, $, $$, actions, listen, every} = context;
  function renderEnvironment(environment) {
    app.environment = environment;
    ui.projectDir.value = environment.project_dir;
    ui.outputDir.value = environment.output_dir;
    const checks = [
      [ui.projectCheck, environment.checks.project, "프로젝트 확인됨", "프로젝트 구조 확인 필요"],
      [ui.outputCheck, environment.checks.output, "폴더 확인됨", "폴더를 찾지 못함"],
    ];
    for (const [element, ok, yes, no] of checks) {
      element.className = ok ? "ok" : "bad";
      element.textContent = ok ? yes : no;
    }
  }

  $("#cancelSystemButton").addEventListener("click", () => {
    renderEnvironment(app.environment);
    ui.systemMessage.textContent = "변경을 취소했습니다.";
    ui.systemDialog.dispatchEvent(new Event("settings-reset"));
  });
  for (const field of [ui.projectDir, ui.outputDir]) {
    field.addEventListener("input", () => {
      field.value = field.value.replace(/[\r\n]+/g, "");
    });
    field.addEventListener("keydown", (event) => {
      if (event.key === "Enter") event.preventDefault();
    });
  }
  let notice;
  function showRestartNotice() {
    if(notice)return;
    notice=document.createElement('aside');notice.className='settings-restart-notice';notice.setAttribute('aria-label','설정 변경 적용');
    const text=document.createElement('p');text.textContent='변경한 내용을 반영하여 지금 재시작하시겠습니까? 작업은 손실되지 않고 이어서 실행됩니다.';
    const status=document.createElement('p');status.className='restart-status';status.setAttribute('role','status');
    const button=document.createElement('button');button.type='button';button.textContent='재시작';
    button.addEventListener('click',async()=>{
      button.disabled=true;status.textContent='작업을 보존하고 서버를 다시 시작합니다…';
      try{
        const data=await requestJson('/api/restart',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
        await waitForServerRestart(data.instance_id);
      }catch(error){status.textContent=error.message;button.disabled=false;}
    });
    notice.append(text,button,status);document.body.append(notice);
  }
  async function waitForServerRestart(previousInstance) {
    const deadline=Date.now()+120000;
    while(Date.now()<deadline){
      await new Promise(resolve=>window.setTimeout(resolve,1500));
      try{
        const response=await fetch(`/api/bootstrap?restart=${Date.now()}`,{cache:'no-store'});
        if(response.ok){const data=await response.json();if(data.instance_id&&data.instance_id!==previousInstance){window.location.reload();return;}}
      }catch(_){/* Expected while the server is restarting. */}
    }
    throw new Error('서버 재시작을 확인하지 못했습니다. start.command 실행 창을 확인해 주세요.');
  }
  listen(document,'settings-saved',showRestartNotice);
  requestJson('/api/bootstrap').then(data=>{if(data.restart_required)showRestartNotice();}).catch(()=>{});

  listen(ui.systemForm, "submit", async (event) => {
    event.preventDefault();
    ui.saveSystemButton.disabled = true;
    ui.systemMessage.className = "";
    ui.systemMessage.textContent = "경로를 확인하는 중";
    try {
      const data = await requestJson("/api/environment", {
        method: "PUT",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          project_dir: ui.projectDir.value,
          output_dir: ui.outputDir.value,
        }),
      });
      renderEnvironment(data.environment);
      ui.systemMessage.textContent = '저장했습니다. 재시작하면 적용됩니다.';
      showRestartNotice();
    } catch (error) {
      ui.systemMessage.className = "error";
      ui.systemMessage.textContent = error.message;
    } finally {
      ui.saveSystemButton.disabled = false;
    }
  });

  return {renderEnvironment, waitForServerRestart};
}
