
export function createShell(context) {
  const {app, ui, $, $$, actions, listen, every} = context;
  listen(ui.closeReviewButton, "click", () => {
    actions.stopReviewAudio();
    app.reviewingRecordingId = null;
    ui.reviewDialog.hidden = true;
    const entry = app.libraryEntries.find((item) => item.key === app.selectedLibraryKey);
    if (entry) actions.renderLibraryDetail(entry);
  });
  listen(document, "keydown", (event) => {
    if (event.key !== "Escape" || !app.reviewingRecordingId || app.activeView !== "recordings") return;
    if (document.querySelector("dialog[open]")) return;
    event.preventDefault();
    ui.closeReviewButton.click();
  });
  listen(ui.guideButton, "click", (event) => {
    event.preventDefault();
    ui.guideFrame.src = "/guide?embedded=1";
    ui.guideDialog.showModal();
  });
  listen(ui.closeGuideButton, "click", () => ui.guideDialog.close());
  listen(ui.guideDialog, "click", (event) => {
    if (event.target === ui.guideDialog) ui.guideDialog.close();
  });
  listen(ui.guideDialog, "close", () => ui.guideFrame.removeAttribute("src"));
  listen(ui.closePipelineButton, "click", () => ui.pipelineDialog.close());
  listen(ui.pipelineDialog, "click", (event) => {
    if (event.target === ui.pipelineDialog) ui.pipelineDialog.close();
  });
  listen(ui.pipelineDialog, "close", () => {
    ui.pipelineDialogBody.replaceChildren();
    const invoker = app.pipelineLogInvoker;
    app.pipelineLogInvoker = null;
    if (invoker?.isConnected) invoker.focus({preventScroll: true});
  });
  listen(document, "click", (event) => {
    $$(".detail-menu[open]").forEach((menu) => {
      if (!menu.contains(event.target)) menu.removeAttribute("open");
    });
    if (!$("#dockNotePicker").contains(event.target)) $("#dockNotePicker").removeAttribute("open");
    if (ui.dockCoursePicker.open && !ui.dockCoursePicker.contains(event.target)) {
      ui.dockCoursePicker.removeAttribute("open");
    }
  });

  function showToast(message, kind = "info", action = null) {
    const toast = document.createElement("div");
    toast.className = `toast ${kind}`;
    const copy = document.createElement("span");
    copy.textContent = message;
    toast.append(copy);
    if (action?.label && typeof action.run === "function") {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = action.label;
      button.addEventListener("click", () => {
        action.run();
        toast.remove();
      });
      toast.append(button);
    }
    ui.toastRegion.append(toast);
    window.setTimeout(() => toast.remove(), 4200);
  }

  function confirmAction(message, title = "확인", acceptLabel = "확인") {
    ui.confirmTitle.textContent = title;
    ui.confirmMessage.textContent = message;
    ui.confirmAccept.textContent = acceptLabel;
    ui.confirmDialog.returnValue = "";
    ui.confirmDialog.showModal();
    return new Promise((resolve) => {
      listen(ui.confirmDialog, "close", () => {
        resolve(ui.confirmDialog.returnValue === "confirm");
      }, {once: true});
    });
  }

  let backdropDialog = null;
  listen(document, "pointerdown",event=>{
    const d=event.target;
    const r=d instanceof HTMLDialogElement ? d.getBoundingClientRect() : null;
    backdropDialog=r && (event.clientX<r.left || event.clientX>r.right || event.clientY<r.top || event.clientY>r.bottom) ? d : null;
  },true);
  listen(document, "click",event=>{
    if(event.target!==backdropDialog || !backdropDialog?.open)return;
    if(backdropDialog===ui.uploadDialog && app.uploadInProgress)return;
    backdropDialog.close(); backdropDialog=null;
  },true);


  return {confirmAction, showToast};
}
