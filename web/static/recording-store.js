const DATABASE_NAME = "lecorder-browser-recovery";
const DATABASE_VERSION = 1;
let databasePromise = null;

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = window.indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.addEventListener("upgradeneeded", () => {
      const database = request.result;
      if (!database.objectStoreNames.contains("chunks")) {
        database.createObjectStore("chunks", {keyPath: ["recordingId", "index"]});
      }
      if (!database.objectStoreNames.contains("sessions")) {
        database.createObjectStore("sessions", {keyPath: "recordingId"});
      }
    });
    request.addEventListener("success", () => resolve(request.result), {once: true});
    request.addEventListener("error", () => reject(request.error || new Error("녹음 복구 저장소를 열지 못했습니다.")), {once: true});
    request.addEventListener("blocked", () => reject(new Error("녹음 복구 저장소 업데이트가 차단됐습니다.")), {once: true});
  });
}

function database() {
  if (!window.indexedDB) return Promise.reject(new Error("이 브라우저는 녹음 복구 저장소를 지원하지 않습니다."));
  if (!databasePromise) {
    databasePromise = openDatabase();
    databasePromise.catch(() => { databasePromise = null; });
  }
  return databasePromise;
}

function runTransaction(storeNames, mode, operation) {
  return database().then((connection) => new Promise((resolve, reject) => {
    const transaction = connection.transaction(storeNames, mode);
    operation(transaction, resolve, reject);
    transaction.addEventListener("complete", () => resolve(), {once: true});
    transaction.addEventListener("error", () => reject(transaction.error), {once: true});
    transaction.addEventListener("abort", () => reject(transaction.error), {once: true});
  }));
}

export function putRecordingSession(session) {
  return runTransaction("sessions", "readwrite", (transaction) => {
    transaction.objectStore("sessions").put({...session, updatedAt: Date.now()});
  });
}

export function updateRecordingSession(recordingId, changes) {
  return runTransaction("sessions", "readwrite", (transaction) => {
    const store = transaction.objectStore("sessions");
    const request = store.get(recordingId);
    request.addEventListener("success", () => {
      store.put({recordingId, ...(request.result || {}), ...changes, updatedAt: Date.now()});
    }, {once: true});
  });
}

export function putPendingChunk(item) {
  return runTransaction("chunks", "readwrite", (transaction) => {
    transaction.objectStore("chunks").put(item);
  });
}

export async function pendingChunks(recordingId) {
  const connection = await database();
  return new Promise((resolve, reject) => {
    const transaction = connection.transaction("chunks", "readonly");
    const range = window.IDBKeyRange.bound(
      [recordingId, 0],
      [recordingId, Number.MAX_SAFE_INTEGER]
    );
    const request = transaction.objectStore("chunks").getAll(range);
    request.addEventListener("success", () => {
      resolve((request.result || []).sort((left, right) => left.index - right.index));
    }, {once: true});
    request.addEventListener("error", () => reject(request.error), {once: true});
  });
}

export function deletePendingChunk(recordingId, index) {
  return runTransaction("chunks", "readwrite", (transaction) => {
    transaction.objectStore("chunks").delete([recordingId, index]);
  });
}

export async function recordingSessions() {
  const connection = await database();
  return new Promise((resolve, reject) => {
    const transaction = connection.transaction("sessions", "readonly");
    const request = transaction.objectStore("sessions").getAll();
    request.addEventListener("success", () => resolve(request.result || []), {once: true});
    request.addEventListener("error", () => reject(request.error), {once: true});
  });
}

export function deleteRecordingRecovery(recordingId) {
  return runTransaction(["chunks", "sessions"], "readwrite", (transaction) => {
    const range = window.IDBKeyRange.bound(
      [recordingId, 0],
      [recordingId, Number.MAX_SAFE_INTEGER]
    );
    transaction.objectStore("chunks").delete(range);
    transaction.objectStore("sessions").delete(recordingId);
  });
}
