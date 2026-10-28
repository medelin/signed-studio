// Muse Studio Lite — approver key and signing, shared by the studio, inbox and campaign
// pages. The key is a WebCrypto Ed25519 pair generated per session with
// extractable=false: the private key never leaves the browser. Only the public key is
// sent (POST /api/keys). The browser signs the exact bytes the server serves at
// /api/approvals/{id}/payload; the server verifies and consumes the one-time nonce.
"use strict";

const Signer = (() => {
  let approverId = null;
  let keyPair = null;

  function b64url(bytes) {
    let s = "";
    for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
    return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function hex(bytes) {
    return Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, "0")).join("");
  }

  async function postJSON(url, body) {
    const r = await fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    let data = {};
    try { data = await r.json(); } catch (_) { /* empty body */ }
    return { ok: r.ok, status: r.status, data };
  }

  async function newKey() {
    return crypto.subtle.generateKey({ name: "Ed25519" }, false, ["sign", "verify"]);
  }

  // The CryptoKey pair is stored in IndexedDB as an opaque, non-extractable object, so a
  // reload in the same session keeps the same registered key. Storage may be unavailable
  // (private windows): then the key lives for this page only.
  function idb() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open("muse-studio-approver", 1);
      req.onupgradeneeded = () => req.result.createObjectStore("keys");
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function idbKey(action, value) {
    try {
      const db = await idb();
      return await new Promise((resolve, reject) => {
        const tx = db.transaction("keys", action === "get" ? "readonly" : "readwrite");
        const store = tx.objectStore("keys");
        const req = action === "get" ? store.get("approver")
          : action === "put" ? store.put(value, "approver") : store.delete("approver");
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      });
    } catch (_) {
      return undefined;
    }
  }

  async function registerKey() {
    if (!window.crypto || !crypto.subtle) throw new Error(T("no_webcrypto"));
    if (!keyPair) keyPair = (await idbKey("get")) || null;
    for (let attempt = 0; attempt < 2; attempt++) {
      if (!keyPair) { keyPair = await newKey(); await idbKey("put", keyPair); }
      const raw = await crypto.subtle.exportKey("raw", keyPair.publicKey);  // public half only
      const r = await postJSON("/api/keys", { public_key: b64url(raw) });
      if (r.ok) { approverId = r.data.approver; return approverId; }
      if (r.status === 409) { keyPair = null; await idbKey("delete"); continue; }  // new pair
      throw new Error(T("key_registration_failed", { error: E(r.data, r.status) }));
    }
    throw new Error(T("key_registration_failed", { error: "" }));
  }

  async function login(username, password) {
    return postJSON("/login", { username, password });
  }

  // The exact bytes to sign and the content they commit to (by hash).
  async function loadPayload(pendingId) {
    if (!approverId) await registerKey();
    const r = await fetch("/api/approvals/" + encodeURIComponent(pendingId) + "/payload?approver=" + encodeURIComponent(approverId), { credentials: "same-origin" });
    const data = await r.json();
    if (!r.ok) throw new Error(T("cannot_load_approval", { error: E(data, r.status) }));
    return data;
  }

  async function sign(payload) {
    if (!keyPair) throw new Error(T("no_key"));
    const bytes = Uint8Array.from(atob(payload.message_b64), (c) => c.charCodeAt(0));
    const sig = await crypto.subtle.sign({ name: "Ed25519" }, keyPair.privateKey, bytes);
    const r = await postJSON("/api/approvals/" + encodeURIComponent(payload.approval_id),
                             { approver: approverId, signature: hex(sig) });
    if (!r.ok) throw new Error(T("approval_refused", { error: E(r.data, r.status) }));
    return r.data;
  }

  return { registerKey, login, loadPayload, sign, postJSON, approver: () => approverId };
})();
