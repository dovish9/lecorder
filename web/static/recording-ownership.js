// Browser storage is shared by tabs; only its owner may finish a live capture.
export async function acquireRecordingOwnership(recordingId) {
  if (!globalThis.navigator?.locks) return null;
  return new Promise((resolve, reject) => {
    navigator.locks.request(`lecorder-recording:${recordingId}`, {ifAvailable: true}, lock => {
      if (!lock) { resolve(null); return; }
      return new Promise(release => resolve(release));
    }).catch(reject);
  });
}
