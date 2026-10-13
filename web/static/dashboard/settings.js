import {requestJson} from "./api.js";

export function createSettings(context) {
  const {app, ui, $, $$, actions, listen, every} = context;
  function renderEnvironment(environment) {
    app.environment = environment;
    ui.projectDir.value = environment.project_dir;
    ui.outputDir.value = environment.output_dir;
    const checks = [
      [ui.projectCheck, environment.checks.project, "프로젝트 확인됨", "app.py를 찾지 못함"],
      [ui.whisperCheck, environment.checks.whisper && environment.checks.model && environment.checks.vad_model, "서버·large-v3·VAD 확인됨", "빌드 또는 모델 확인 필요"],
      [ui.outputCheck, environment.checks.output, "폴더 확인됨", "폴더를 찾지 못함"],
    ];
    for (const [element, ok, yes, no] of checks) {
      element.className = ok ? "ok" : "bad";
      element.textContent = ok ? yes : no;
    }
  }

  listen(ui.systemButton, "click", () => {
    renderEnvironment(app.environment);
    ui.systemMessage.textContent = "";
    ui.systemDialog.showModal();
  });
  listen(ui.closeSystemButton, "click", () => ui.systemDialog.close());
  $("#cancelSystemButton").addEventListener("click", () => ui.systemDialog.close());
  for (const field of [ui.projectDir, ui.outputDir]) {
    field.addEventListener("input", () => {
      field.value = field.value.replace(/[\r\n]+/g, "");
    });
    field.addEventListener("keydown", (event) => {
      if (event.key === "Enter") event.preventDefault();
    });
  }
  listen(ui.systemDialog, "click", (event) => {
    if (event.target === ui.systemDialog) ui.systemDialog.close();
  });

  async function waitForServerRestart() {
    await new Promise((resolve) => window.setTimeout(resolve, 1200));
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      try {
        const response = await fetch(`/api/bootstrap?restart=${Date.now()}`, {cache: "no-store"});
        if (response.ok) {
          window.location.reload();
          return;
        }
      } catch (_) {
        // A connection failure is expected while the managed servers restart.
      }
      await new Promise((resolve) => window.setTimeout(resolve, 500));
    }
    ui.systemMessage.className = "error";
    ui.systemMessage.textContent = "자동 재시작을 확인하지 못했습니다. start.command를 다시 실행해 주세요.";
    ui.saveSystemButton.disabled = false;
  }

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
      if (data.restart_scheduled) {
        ui.systemMessage.textContent = "저장됨 · 서버를 다시 시작하는 중";
        void waitForServerRestart();
        return;
      }
      ui.systemMessage.textContent = "저장됨 · start.command로 실행해야 자동 재시작됩니다.";
    } catch (error) {
      ui.systemMessage.className = "error";
      ui.systemMessage.textContent = error.message;
    } finally {
      if (!ui.systemMessage.textContent.includes("다시 시작하는 중")) ui.saveSystemButton.disabled = false;
    }
  });

  return {renderEnvironment, waitForServerRestart};
}
