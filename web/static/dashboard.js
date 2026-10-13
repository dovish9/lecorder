import {createState} from "./dashboard/state.js";
import {setupAISettings} from "./ai-settings.js";
import {createShell} from "./dashboard/shell.js";
import {createRouting} from "./dashboard/routing.js";
import {createCourses} from "./dashboard/courses.js";
import {createLive} from "./dashboard/live.js";
import {createCapture} from "./dashboard/capture.js";
import {createPlayer} from "./dashboard/player.js";
import {createLibrary} from "./dashboard/library.js";
import {createUpload} from "./dashboard/upload.js";
import {createSettings} from "./dashboard/settings.js";
import {createActivity} from "./dashboard/activity.js";
import {createReview} from "./dashboard/review.js";
import {createNotifications} from "./dashboard/notifications.js";
import {createStatus} from "./dashboard/status.js";

const controller = new AbortController();
const timers = new Set();
const context = {...createState(), actions: {},
  listen(target, type, handler, options = {}) {
    target.addEventListener(type, handler, {...(typeof options === 'boolean' ? {capture: options} : options), signal: controller.signal});
  },
  every(callback, delay) {
    const timer = window.setInterval(callback, delay);
    timers.add(timer);
    return timer;
  },
};
const systemDarkMode = window.matchMedia("(prefers-color-scheme: dark)");
function syncThemeColor() {
  document.querySelector("meta[name='theme-color']").content = systemDarkMode.matches ? "#1e1f22" : "#ecedef";
}
syncThemeColor();
context.listen(systemDarkMode, "change", syncThemeColor);
Object.assign(context.actions, createShell(context));
Object.assign(context.actions, createRouting(context));
Object.assign(context.actions, createCourses(context));
Object.assign(context.actions, createLive(context));
Object.assign(context.actions, createCapture(context));
Object.assign(context.actions, createPlayer(context));
Object.assign(context.actions, createLibrary(context));
Object.assign(context.actions, createUpload(context));
Object.assign(context.actions, createSettings(context));
Object.assign(context.actions, createActivity(context));
Object.assign(context.actions, createReview(context));
Object.assign(context.actions, createNotifications(context));
Object.assign(context.actions, createStatus(context));

window.addEventListener("pagehide", event => {
  if (event.persisted) return;
  context.actions.stopLive();
  controller.abort();
  timers.forEach(window.clearInterval);
});
void context.actions.initialize();
setupAISettings();
