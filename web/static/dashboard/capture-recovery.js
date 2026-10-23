import {requestJson} from './api.js';

// Durable browser receipts and retries are independent of microphone capture.
export function createCaptureRecovery({app,ui,actions,store,acquireOwnership,requestJsonBeforeDeadline}) {
  const {deletePendingChunk,deleteRecordingRecovery,pendingChunks,putPendingChunk,recordingSessions,updateRecordingSession}=store;
  async function uploadChunk(recordingId, index, blob, {attempts = 3, timeoutMs = 5000} = {}) {
    let lastError;
    for (let attempt = 1; attempt <= attempts; attempt += 1) {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
      try {
        return await requestJson(`/api/recordings/${recordingId}/chunks/${index}`, {
          method: "PUT",
          headers: {"Content-Type": blob.type || "application/octet-stream"},
          body: blob,
          signal: controller.signal,
        });
      } catch (error) {
        lastError = error.name === "AbortError"
          ? new Error("서버 응답 시간이 초과됐습니다.")
          : error;
        if (attempt < attempts) await new Promise((resolve) => window.setTimeout(resolve, attempt * 500));
      } finally {
        window.clearTimeout(timeout);
      }
    }
    throw lastError;
  }

  function queueRecordingChunk(blob) {
    if (!blob.size || !app.recordingId) return;
    const item = {recordingId: app.recordingId, index: app.chunkIndex, blob};
    app.chunkIndex += 1;
    const nextChunkIndex = app.chunkIndex;
    const durationSeconds = Math.max(0, (app.pausedAt || Date.now()) - app.recordingStartedAt - app.pausedDuration) / 1000;
    // Persist immediately. A stalled upload must never hold up later chunks from
    // reaching durable browser storage.
    const persistence = (async () => {
      try {
        await putPendingChunk(item);
        await updateRecordingSession(item.recordingId, {
          durationSeconds, nextChunkIndex,
        });
        return true;
      } catch (_) {
        if (!app.failedChunks.some((chunk) => chunk.recordingId === item.recordingId && chunk.index === item.index)) {
          app.failedChunks.push(item);
        }
        return false;
      }
    })();
    app.chunkUploadChain = app.chunkUploadChain.then(async () => {
      const persisted = await persistence;
      try {
        await uploadChunk(item.recordingId, item.index, item.blob, {attempts: 1, timeoutMs: 4000});
        if (persisted) await deletePendingChunk(item.recordingId, item.index).catch(() => {});
        app.failedChunks = app.failedChunks.filter(
          (chunk) => chunk.recordingId !== item.recordingId || chunk.index !== item.index
        );
        ui.dockRecordingSession.dataset.sync = "saved";
        ui.dockRecordingSession.setAttribute("aria-label", `녹음 중 · ${app.chunkIndex}개 조각 저장됨`);
      } catch (error) {
        ui.dockRecordingSession.dataset.sync = "error";
        const message = persisted
          ? "서버 연결 끊김 · 녹음 조각은 브라우저에 보관 중입니다."
          : "서버 연결 끊김 · 일부 조각을 메모리에 보관 중이므로 탭을 닫지 마세요.";
        ui.dockRecordingSession.setAttribute("aria-label", message);
        ui.dockRecordingSession.title = message;
      }
    });
  }

  async function retryFailedChunks(recordingId) {
    let durable = [];
    try {
      durable = await pendingChunks(recordingId);
    } catch (_) {
      // Memory fallback below remains intact until each upload succeeds.
    }
    const combined = new Map();
    for (const item of durable) combined.set(item.index, item);
    for (const item of app.failedChunks) {
      if (item.recordingId === recordingId) combined.set(item.index, item);
    }
    let receipts=[];
    if(combined.size) {
      try {receipts=(await requestJson(`/api/recordings/${recordingId}/chunks`)).chunks || [];} catch (_) { /* Retry the durable PUT instead. */ }
    }
    for (const item of [...combined.values()].sort((left, right) => left.index - right.index)) {
      const receipt=receipts.find(r=>r.number===item.index && r.size===item.blob.size);
      let confirmed=false;
      if(receipt && globalThis.crypto?.subtle) {
        const bytes=await crypto.subtle.digest('SHA-256',await item.blob.arrayBuffer());
        confirmed=Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('')===receipt.sha256;
      }
      if(!confirmed) await uploadChunk(recordingId, item.index, item.blob);
      await deletePendingChunk(recordingId, item.index).catch(() => {});
      app.failedChunks = app.failedChunks.filter(
        (chunk) => chunk.recordingId !== recordingId || chunk.index !== item.index
      );
    }
  }

  function restoreRecordingSession(session) {
    const existing = app.recoveryTasks.get(session.recordingId);
    if (existing) return existing;
    const task = (async () => {
      const release = await acquireOwnership(session.recordingId);
      if (!release) return null; // Another tab is still recording; never finalize it.
      try {
        await retryFailedChunks(session.recordingId);
        const result = await requestJson(`/api/recordings/${session.recordingId}/finalize`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({duration_seconds: Number(session.durationSeconds) || 0, recovered:true}),
        });
        await deleteRecordingRecovery(session.recordingId).catch(() => {});
        return result;
      } finally { release(); }
    })();
    app.recoveryTasks.set(session.recordingId, task);
    void task.finally(() => app.recoveryTasks.delete(session.recordingId)).catch(() => {});
    return task;
  }

  let recoveryPromise = null;
  function recoverRecordingSessions(recordings) {
    if (recoveryPromise) return recoveryPromise;
    recoveryPromise = recoverSessions(recordings).finally(() => { recoveryPromise = null; });
    return recoveryPromise;
  }

  async function recoverSessions(recordings) {
    let complete = true;
    app.recoverySyncing = true;
    try {
      let sessions = [];
      try {
        sessions = await recordingSessions();
      } catch (_) {
        return false;
      }
      for (const session of sessions) {
        let recording = recordings.find((item) => item.id === session.recordingId);
        if (!recording) {
          try { recording = (await requestJson(`/api/recordings/${encodeURIComponent(session.recordingId)}`)).recording; }
          catch (_) { complete = false; continue; }
        }
        if (!recording) continue; // A partial status listing is not a server receipt.
        if (["queued", "processing", "cancelling", "cancelled", "completed", "failed"].includes(recording.status)) {
          if(recording.status==='cancelled' || !(await pendingChunks(session.recordingId)).length) {
            await deleteRecordingRecovery(session.recordingId).catch(() => {});
          }
          continue;
        }
        const activelyRecording = session.recordingId === app.recordingId
          && app.mediaRecorder
          && app.mediaRecorder.state !== "inactive";
        if (session.recordingId === app.finalizingRecordingId
            || (session.recordingId === app.recordingId && app.recordingState === "starting")) continue;
        if (activelyRecording) {
          app.chunkUploadChain = app.chunkUploadChain.then(
            () => retryFailedChunks(session.recordingId)
          );
          try {
            await app.chunkUploadChain;
            ui.dockRecordingSession.dataset.sync = "saved";
          } catch (_) {
            // The durable chunks stay in IndexedDB for the next status refresh.
          }
          continue;
        }
        if (!["recording", "recoverable"].includes(recording.status)) continue;
        if (app.recoveryAttempted.has(session.recordingId)) { complete = false; continue; }
        app.recoveryAttempted.add(session.recordingId);
        try {
          const result = await restoreRecordingSession(session);
          if (!result) { app.recoveryAttempted.delete(session.recordingId); continue; }
          actions.renderRecordings([
            result.recording,
            ...app.recordings.filter((item) => item.id !== session.recordingId),
          ]);
          actions.showToast(`중단됐던 ‘${recording.title}’ 녹음을 복구해 음성 변환 대기열에 추가했습니다.`, "success");
        } catch (error) {
          complete = false;
          actions.showToast(`녹음 자동 복구 실패: ${error.message}`, "error");
          window.setTimeout(() => app.recoveryAttempted.delete(session.recordingId), 10000);
        }
      }
    } finally {
      app.recoverySyncing = false;
    }
    return complete;
  }

  return {uploadChunk,queueRecordingChunk,retryFailedChunks,restoreRecordingSession,recoverRecordingSessions};
}
