'use strict';

// Both copies use the same revision. A tab must finish reading them before it can save.
function createProgressStore(localKey, decode, onPeerChange, onStatus) {
  const databaseName = 'examen-civique-cr';
  const table = 'progress';
  const key = 'current';
  const writer = Math.random().toString(36).slice(2);
  let database = null;
  let channel = null;
  let current = null;
  let signature = '';
  let localOK = false;
  let databaseOK = false;

  function notify() { onStatus(databaseOK, localOK); }

  function unpack(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    try {
      const rawVersion = value._crVersion;
      const validVersion = Array.isArray(rawVersion) && Number.isSafeInteger(rawVersion[0]) &&
        rawVersion[0] >= 0 && typeof rawVersion[1] === 'string';
      const version = validVersion ? rawVersion : [0, ''];
      return { version, data: decode(value) };
    } catch { return null; }
  }

  function newer(a, b) {
    if (!a) return false;
    if (!b) return true;
    return a.version[0] > b.version[0] ||
      (a.version[0] === b.version[0] && a.version[1] > b.version[1]);
  }

  function readMirror() {
    try { return unpack(JSON.parse(localStorage.getItem(localKey))); }
    catch { return null; }
  }

  function adopt(record, render) {
    if (!newer(record, current)) return;
    current = record;
    signature = JSON.stringify(record.data);
    if (render) onPeerChange(record.data);
  }

  function openDatabase() {
    return new Promise(resolve => {
      let settled = false;
      const finish = value => {
        if (settled) { if (value) value.close(); return; }
        settled = true;
        clearTimeout(timeout);
        resolve(value);
      };
      const timeout = setTimeout(() => finish(null), 2400);
      try {
        const request = indexedDB.open(databaseName, 1);
        request.onupgradeneeded = () => request.result.createObjectStore(table);
        request.onsuccess = () => finish(request.result);
        request.onerror = request.onblocked = () => finish(null);
      } catch { finish(null); }
    });
  }

  function readPrimary() {
    return new Promise(resolve => {
      if (!database) { resolve(null); return; }
      try {
        const tx = database.transaction(table, 'readonly');
        const request = tx.objectStore(table).get(key);
        tx.oncomplete = () => resolve(unpack(request.result));
        tx.onerror = tx.onabort = () => resolve(null);
      } catch { resolve(null); }
    });
  }

  function writeCopies(record) {
    // Write the small synchronous mirror before yielding to Safari on background/close.
    const flat = { ...record.data, _crVersion: record.version };
    try {
      localStorage.setItem(localKey, JSON.stringify(flat));
      localOK = true;
    } catch { localOK = false; }
    notify();
    if (!database) return;
    try {
      const tx = database.transaction(table, 'readwrite');
      const store = tx.objectStore(table);
      const request = store.get(key);
      let winner;
      request.onsuccess = () => {
        winner = unpack(request.result);
        if (!newer(winner, record)) {
          winner = null;
          store.put(flat, key);
        }
      };
      tx.oncomplete = () => {
        databaseOK = true;
        if (winner) adopt(winner, true);
        else if (channel) channel.postMessage(record.version);
        notify();
      };
      tx.onerror = tx.onabort = () => { databaseOK = false; notify(); };
    } catch { databaseOK = false; notify(); }
  }

  async function load() {
    database = await openDatabase();
    if (database) database.onversionchange = () => {
      database.close();
      database = null;
      databaseOK = false;
      notify();
    };
    const primary = await readPrimary();
    const mirror = readMirror();
    let best = newer(mirror, primary) ? mirror : primary;
    if (!best) {
      let done = [];
      try { done = JSON.parse(localStorage.getItem('civicCRProgress2026'))?.done || []; }
      catch { /* An older installation is optional. */ }
      best = { version: [0, ''], data: decode({done: Array.isArray(done) ? done : [], history: [], missed: []}) };
    }
    adopt(best, false);
    writeCopies(best);
    window.addEventListener('storage', event => {
      if (event.key === localKey && event.newValue) {
        try { adopt(unpack(JSON.parse(event.newValue)), true); } catch { /* Ignore invalid copies. */ }
      }
    });
    try {
      channel = new BroadcastChannel(databaseName + '-changes');
      channel.onmessage = async event => {
        if (Array.isArray(event.data) && newer({ version: event.data }, current)) {
          adopt(await readPrimary(), true);
        }
      };
    } catch { /* The local storage event works without BroadcastChannel. */ }
    return best.data;
  }

  function save(data) {
    if (!current) return;
    const json = JSON.stringify(data);
    if (json === signature) return;
    const mirror = readMirror();
    if (newer(mirror, current)) { adopt(mirror, true); return; }
    const version = [Math.max(Date.now(), current.version[0] + 1), writer];
    // Keep an immutable snapshot for pending IndexedDB transactions.
    current = { version, data: JSON.parse(json) };
    signature = json;
    writeCopies(current);
  }

  return { load, save };
}
