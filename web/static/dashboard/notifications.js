import {openNote} from "../notes.js";
import {recordingWorkflowStatus} from "./selectors.js";

export function createNotifications(context) {
  const {actions} = context;
  const browser = context.browser || window;
  const Notification = browser.Notification;
  let datasetId=null;
  const stateKey=()=>`lecorder-completion-notification-states:${datasetId}`;
  function loadCompletionNotificationStates() {
    try {
      return JSON.parse(browser.localStorage.getItem(stateKey()) || "{}");
    } catch (_) {
      return {};
    }
  }

  let completionNotificationStates = {};

  function saveCompletionNotificationStates() {
    try {
      browser.localStorage.setItem(stateKey(), JSON.stringify(completionNotificationStates));
    } catch (_) {
      // Notifications can still work for this open page if storage is unavailable.
    }
  }

  function notificationPermission() {
    return "Notification" in browser ? Notification.permission : "unsupported";
  }

  function notifyCompletion(title, body, open) {
    if (notificationPermission() !== "granted") return;
    try {
      const notification = new Notification(title, {body, tag: `lecorder-${title}-${body}`});
      notification.addEventListener("click", () => {
        browser.focus();
        open();
        notification.close();
      });
      notification.addEventListener("error", () => actions.showToast("브라우저 알림을 표시하지 못했습니다. 사이트 및 운영체제 알림 설정을 확인하세요.", "error"));
    } catch (error) {
      actions.showToast(`브라우저 알림 발송 실패: ${error.message}`, "error");
    }
  }

  function detectCompletionNotifications(data) {
    if(data.dataset_id!==datasetId){datasetId=data.dataset_id;completionNotificationStates=loadCompletionNotificationStates();}
    const next = {};
    for (const recording of data.recordings || []) {
      const key = `recording:${recording.id}`;
      const previous = completionNotificationStates[key];
      const workflowStatus = recordingWorkflowStatus(recording);
      next[key] = workflowStatus;
      if (previous && previous !== "completed" && workflowStatus === "completed" && recording.review_status !== "cancelled") {
        const completionTitle = {retranscribe: "강의 다시 음성 변환 완료", review: "강의 다시 검수 완료"}[recording.last_operation] || "강의 음성 변환 완료";
        notifyCompletion(completionTitle, `${recording.course_name} · ${recording.title}`, () => {
          browser.location.hash = `#/recordings/job/${encodeURIComponent(recording.id)}`;
        });
      }
      if (previous && previous !== "failed" && workflowStatus === "failed") {
        notifyCompletion("강의 음성 변환 실패", recording.error || `${recording.course_name} · ${recording.title}`, () => {
          browser.location.hash = `#/recordings/job/${encodeURIComponent(recording.id)}`;
        });
      }
    }
    for (const note of data.notes || []) {
      const key = `note:${note.revision_id}`;
      const current = `${note.status}/${note.study_status}`;
      const previous = completionNotificationStates[key];
      next[key] = current;
      if (previous && previous !== "ready/completed" && previous !== "partial/completed" &&
          ["ready/completed", "partial/completed"].includes(current)) {
        notifyCompletion("강의노트 분석 완료", `${note.course_name} · ${note.title}`, () => {
          browser.location.hash = actions.courseRoute(note.course_id);
          browser.setTimeout(() => { void openNote(note.note_id); }, 0);
        });
      }
      if (previous && previous !== current && (note.status === "failed" || note.study_status === "failed")) {
        notifyCompletion("강의노트 분석 실패", note.error || `${note.course_name} · ${note.title}`, () => {
          browser.location.hash = actions.courseRoute(note.course_id);
          browser.setTimeout(() => { void openNote(note.note_id); }, 0);
        });
      }
    }
    completionNotificationStates = next;
    saveCompletionNotificationStates();
  }


  return {detectCompletionNotifications};
}
