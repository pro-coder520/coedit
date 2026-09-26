const editor = document.querySelector("#editor");
const roomInput = document.querySelector("#room");
const connection = document.querySelector("#connection");
const connectionLabel = document.querySelector("#connection-label");
const sequenceLabel = document.querySelector("#sequence");
const peopleList = document.querySelector("#people");
const activity = document.querySelector("#activity");

const rootKey = "root";
const nodes = new Map();
const children = new Map([[rootKey, []]]);
const seenOperations = new Map();
const pendingOperations = new Map();
const receivedSequences = new Set();
const users = new Set();
const intentionalClose = new WeakSet();
let socket;
let activeRoom = "";
let reconnectTimer;
let retryCount = 0;
let lastSequence = 0;
let clock = Number(localStorage.getItem("coedit-counter") || 0);
let typingTimer;
let activityTimer;
let ready = false;

let clientId = localStorage.getItem("coedit-client-id");
if (!clientId) {
  clientId = crypto.randomUUID();
  localStorage.setItem("coedit-client-id", clientId);
}
const userId = `user-${clientId.slice(0, 6)}`;

function idKey(id) {
  return id === null ? rootKey : JSON.stringify([id.client_id, id.counter]);
}

function compareIds(left, right) {
  return left.counter - right.counter || left.client_id.localeCompare(right.client_id);
}

function operationId(operation) {
  return operation.type === "insert" ? operation.element_id : operation.operation_id;
}

function defer(dependency, operation) {
  const key = idKey(dependency);
  const pending = pendingOperations.get(key) || [];
  pending.push(operation);
  pendingOperations.set(key, pending);
}

function applyKnown(operation) {
  if (operation.type === "insert") {
    const parentKey = idKey(operation.after);
    if (parentKey !== rootKey && !nodes.has(parentKey)) {
      defer(operation.after, operation);
      return;
    }
    const key = idKey(operation.element_id);
    nodes.set(key, { id: operation.element_id, value: operation.value, deleted: false });
    children.set(key, children.get(key) || []);
    const siblings = children.get(parentKey) || [];
    siblings.push(operation.element_id);
    children.set(parentKey, siblings);
    const waiting = pendingOperations.get(key) || [];
    pendingOperations.delete(key);
    waiting.sort((a, b) => compareIds(operationId(a), operationId(b)));
    for (const pending of waiting) applyKnown(pending);
    return;
  }
  const target = nodes.get(idKey(operation.target));
  if (!target) {
    defer(operation.target, operation);
    return;
  }
  target.deleted = true;
}

function applyOperation(operation) {
  const id = operationId(operation);
  const key = idKey(id);
  const encoded = JSON.stringify(operation);
  if (seenOperations.has(key)) return false;
  seenOperations.set(key, encoded);
  clock = Math.max(clock, id.counter);
  localStorage.setItem("coedit-counter", String(clock));
  applyKnown(operation);
  return true;
}

function visibleNodes() {
  const result = [];
  const stack = [...(children.get(rootKey) || [])].sort(compareIds);
  while (stack.length) {
    const id = stack.pop();
    const key = idKey(id);
    const node = nodes.get(key);
    if (!node.deleted) result.push(node);
    stack.push(...(children.get(key) || []).slice().sort(compareIds));
  }
  return result;
}

function renderDocument() {
  const start = editor.selectionStart;
  const end = editor.selectionEnd;
  const focused = document.activeElement === editor;
  const value = visibleNodes().map((node) => node.value).join("");
  if (editor.value !== value) {
    const delta = value.length - editor.value.length;
    editor.value = value;
    if (focused) {
      editor.setSelectionRange(
        Math.max(0, Math.min(value.length, start + delta)),
        Math.max(0, Math.min(value.length, end + delta)),
      );
    }
  }
}

function setConnectionState(state, label) {
  connection.dataset.state = state;
  connectionLabel.textContent = label;
  editor.disabled = !ready;
}

function renderUsers() {
  peopleList.replaceChildren();
  for (const user of [...users].sort()) {
    const item = document.createElement("li");
    item.textContent = user;
    peopleList.append(item);
  }
}

function receiveSequence(sequence) {
  if (sequence <= lastSequence) return;
  receivedSequences.add(sequence);
  while (receivedSequences.delete(lastSequence + 1)) lastSequence += 1;
  sequenceLabel.textContent = String(lastSequence);
}

function resetDocument(snapshot) {
  nodes.clear();
  children.clear();
  children.set(rootKey, []);
  seenOperations.clear();
  pendingOperations.clear();
  for (const operation of snapshot.operations) applyOperation(operation);
}

function showActivity(message) {
  activity.textContent = message;
  clearTimeout(activityTimer);
  activityTimer = setTimeout(() => { activity.textContent = ""; }, 1800);
}

function handleMessage(message) {
  if (message.type === "sync") {
    if (message.snapshot) {
      resetDocument(message.snapshot);
      lastSequence = message.snapshot_seq;
      receivedSequences.clear();
    }
    for (const entry of message.operations) {
      applyOperation(entry.operation);
      receiveSequence(entry.seq);
    }
    sequenceLabel.textContent = String(lastSequence);
    renderDocument();
    ready = true;
    setConnectionState("connected", "Connected");
  } else if (message.type === "presence_snapshot") {
    users.clear();
    for (const user of message.users) users.add(user);
    renderUsers();
  } else if (message.type === "operation") {
    applyOperation(message.operation);
    receiveSequence(message.seq);
    renderDocument();
  } else if (message.type === "ack") {
    receiveSequence(message.seq);
  } else if (message.type === "presence") {
    if (message.event === "join") users.add(message.user_id);
    if (message.event === "leave") users.delete(message.user_id);
    if (message.event === "typing" && message.value) showActivity(`${message.user_id} is typing`);
    renderUsers();
  } else if (message.type === "error") {
    showActivity("Operation rejected");
  }
}

function openSocket() {
  const room = roomInput.value.trim() || "demo";
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  const params = new URLSearchParams({ user_id: userId, last_seq: String(lastSequence) });
  const url = `${scheme}//${location.host}/ws/${encodeURIComponent(room)}?${params}`;
  ready = false;
  setConnectionState("connecting", "Connecting");
  const currentSocket = new WebSocket(url);
  socket = currentSocket;
  currentSocket.addEventListener("open", () => {
    retryCount = 0;
    setConnectionState("connecting", "Synchronizing");
  });
  currentSocket.addEventListener("message", (event) => {
    try { handleMessage(JSON.parse(event.data)); }
    catch { showActivity("Invalid server message"); }
  });
  currentSocket.addEventListener("close", () => {
    if (intentionalClose.has(currentSocket)) return;
    ready = false;
    setConnectionState("disconnected", "Reconnecting");
    const delay = Math.min(1000 * (2 ** retryCount), 15000);
    retryCount += 1;
    reconnectTimer = setTimeout(openSocket, delay);
  });
  currentSocket.addEventListener("error", () => setConnectionState("disconnected", "Connection error"));
}

function send(message) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
}

function nextId() {
  clock = Math.max(clock, Number(localStorage.getItem("coedit-counter") || 0)) + 1;
  localStorage.setItem("coedit-counter", String(clock));
  return { client_id: clientId, counter: clock };
}

function sendOperation(operation) {
  applyOperation(operation);
  send(operation);
}

function handleInput() {
  if (!ready || socket?.readyState !== WebSocket.OPEN) return;
  const previous = visibleNodes();
  const before = previous.map((node) => node.value);
  const after = Array.from(editor.value);
  let prefix = 0;
  while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix += 1;
  let suffix = 0;
  while (
    suffix < before.length - prefix && suffix < after.length - prefix &&
    before[before.length - suffix - 1] === after[after.length - suffix - 1]
  ) suffix += 1;

  for (let index = prefix; index < before.length - suffix; index += 1) {
    sendOperation({ type: "delete", operation_id: nextId(), target: previous[index].id });
  }
  let anchor = prefix > 0 ? previous[prefix - 1].id : null;
  for (const value of after.slice(prefix, after.length - suffix)) {
    const elementId = nextId();
    sendOperation({ type: "insert", element_id: elementId, after: anchor, value });
    anchor = elementId;
  }
  send({ type: "presence", event: "cursor", value: editor.selectionStart });
  send({ type: "presence", event: "typing", value: true });
  clearTimeout(typingTimer);
  typingTimer = setTimeout(() => send({ type: "presence", event: "typing", value: false }), 900);
}

document.querySelector("#connect").addEventListener("click", () => {
  clearTimeout(reconnectTimer);
  const room = roomInput.value.trim() || "demo";
  if (socket) {
    intentionalClose.add(socket);
    socket.close();
  }
  if (room !== activeRoom) {
    nodes.clear(); children.clear(); children.set(rootKey, []);
    seenOperations.clear(); pendingOperations.clear();
    users.clear(); renderUsers();
    lastSequence = 0;
    receivedSequences.clear();
  }
  activeRoom = room;
  openSocket();
});

editor.addEventListener("input", handleInput);
editor.addEventListener("keyup", () => send({ type: "presence", event: "cursor", value: editor.selectionStart }));
editor.addEventListener("click", () => send({ type: "presence", event: "cursor", value: editor.selectionStart }));
window.addEventListener("beforeunload", () => { if (socket) intentionalClose.add(socket); socket?.close(); });
setInterval(() => send({ type: "ping" }), 20000);
activeRoom = roomInput.value;
openSocket();