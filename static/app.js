const editor = document.querySelector("#editor");
const board = document.querySelector("#board");
const canvasContext = board.getContext("2d");
const appShell = document.querySelector("#app-shell");
const roomInput = document.querySelector("#room");
const connectButton = document.querySelector("#connect");
const connectionLabel = document.querySelector("#connection-label");
const peopleList = document.querySelector("#people");
const peopleCount = document.querySelector("#people-count");
const colorAssist = document.querySelector("#color-assist");
const swatches = [...document.querySelectorAll(".swatch")];
const toolButtons = [...document.querySelectorAll("[data-tool]")];
const widthInput = document.querySelector("#stroke-width");
const widthOutput = document.querySelector("#width-value");
const drawModeButton = document.querySelector("#draw-mode");
const writeModeButton = document.querySelector("#write-mode");
const undoButton = document.querySelector("#undo");
const redoButton = document.querySelector("#redo");
const activity = document.querySelector("#activity");

const rootKey = "root";
const nodes = new Map();
const children = new Map([[rootKey, []]]);
const seenOperations = new Map();
const pendingOperations = new Map();
const receivedSequences = new Set();
const users = new Set();
const intentionalClose = new WeakSet();
const canvasStrokes = new Map();
const canvasSeen = new Set();
const canvasErased = new Set();
const canvasPending = new Map();
const localCanvasOperations = new Map();
const undoStack = [];
const redoStack = [];
let socket;
let activeRoom = "";
let reconnectTimer;
let retryCount = 0;
let lastSequence = 0;
let lastCanvasSequence = 0;
let clock = Number(localStorage.getItem("coedit-counter") || 0);
let typingTimer;
let activityTimer;
let ready = false;
let textReady = false;
let canvasReady = false;
let presenceReady = false;
let selectedTool = "pen";
let selectedColor = "#263f73";
let strokeWidth = 6;
let activeStroke;
let canvasFrame;
let currentMode = "draw";

let clientId = localStorage.getItem("coedit-client-id");
if (!clientId) {
  clientId = crypto.randomUUID();
  localStorage.setItem("coedit-client-id", clientId);
}
const userId = `user-${clientId.slice(0, 6)}`;
colorAssist.checked = localStorage.getItem("coedit-color-assist") === "true";
document.body.classList.toggle("color-safe", colorAssist.checked);
applyPalette();
colorAssist.addEventListener("change", () => {
  document.body.classList.toggle("color-safe", colorAssist.checked);
  localStorage.setItem("coedit-color-assist", String(colorAssist.checked));
  applyPalette();
});

function applyPalette() {
  const palette = colorAssist.checked
    ? [
        ["#0072b2", "Blue"], ["#d55e00", "Vermilion"], ["#009e73", "Bluish green"],
        ["#e69f00", "Orange"], ["#cc79a7", "Reddish purple"], ["#242c2a", "Charcoal"],
      ]
    : [
        ["#263f73", "Navy blue"], ["#d6533c", "Vermilion"], ["#2b826f", "Jade"],
        ["#e2a52f", "Amber"], ["#69549a", "Indigo"], ["#252b28", "Charcoal"],
      ];
  swatches.forEach((swatch, index) => {
    const [color, name] = palette[index];
    swatch.dataset.color = color;
    swatch.style.setProperty("--swatch", color);
    swatch.setAttribute("aria-label", name);
    swatch.title = name;
  });
  const selected = swatches.find((swatch) => swatch.getAttribute("aria-pressed") === "true");
  if (selected) selectedColor = selected.dataset.color;
}

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
  connectionLabel.textContent = label;
  if (state !== "connected") updateReadyState();
}

function renderUsers() {
  peopleList.replaceChildren();
  const palette = ["#d8f078", "#e7a778", "#9ec6d7", "#eccd65"];
  const orderedUsers = [...users].sort();
  peopleCount.textContent = String(orderedUsers.length);
  orderedUsers.forEach((user, index) => {
    const item = document.createElement("li");
    const avatar = document.createElement("span");
    avatar.className = "person-avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.style.setProperty("--person-color", palette[index % palette.length]);
    avatar.textContent = (user.split(/[-_\s]+/).at(-1) || user).slice(0, 2).toUpperCase();

    const name = document.createElement("span");
    name.className = "person-name";
    name.textContent = user;

    item.append(avatar, name);
    peopleList.append(item);
  });
}

function receiveSequence(sequence) {
  if (sequence <= lastSequence) return;
  receivedSequences.add(sequence);
  while (receivedSequences.delete(lastSequence + 1)) lastSequence += 1;
}

function applyCanvasOperation(operation) {
  const operationKey = idKey(operation.operation_id);
  if (canvasSeen.has(operationKey)) return;
  canvasSeen.add(operationKey);
  if (operation.type === "canvas_clear") {
    canvasStrokes.clear();
    canvasErased.clear();
    return;
  }
  if (operation.type === "stroke") {
    canvasStrokes.set(operationKey, operation);
    return;
  }
  if (operation.type === "canvas_erase") {
    canvasErased.add(idKey(operation.target));
    return;
  }
  if (operation.type === "canvas_restore") {
    for (const stroke of operation.strokes) {
      const strokeKey = idKey(stroke.operation_id);
      canvasStrokes.set(strokeKey, stroke);
      canvasErased.delete(strokeKey);
    }
  }
}

function receiveCanvasOperation(sequence, operation) {
  if (sequence <= lastCanvasSequence) {
    localCanvasOperations.delete(idKey(operation.operation_id));
    return;
  }
  canvasPending.set(sequence, operation);
  while (canvasPending.has(lastCanvasSequence + 1)) {
    const nextSequence = lastCanvasSequence + 1;
    const nextOperation = canvasPending.get(nextSequence);
    canvasPending.delete(nextSequence);
    applyCanvasOperation(nextOperation);
    const operationKey = idKey(nextOperation.operation_id);
    localCanvasOperations.delete(operationKey);
    lastCanvasSequence = nextSequence;
  }
  scheduleCanvasRender();
}

function resizeCanvas() {
  const rect = board.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const pixelRatio = window.devicePixelRatio || 1;
  const width = Math.round(rect.width * pixelRatio);
  const height = Math.round(rect.height * pixelRatio);
  if (board.width !== width || board.height !== height) {
    board.width = width;
    board.height = height;
  }
  canvasContext.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  renderCanvas();
}

function drawStroke(stroke) {
  const rect = board.getBoundingClientRect();
  const points = stroke.points.map((point) => ({ x: point.x * rect.width, y: point.y * rect.height }));
  if (!points.length) return;
  canvasContext.save();
  canvasContext.globalAlpha = stroke.opacity ?? 1;
  canvasContext.strokeStyle = stroke.color;
  canvasContext.fillStyle = stroke.color;
  canvasContext.lineWidth = stroke.width;
  canvasContext.lineCap = "round";
  canvasContext.lineJoin = "round";
  if (points.length === 1) {
    canvasContext.beginPath();
    canvasContext.arc(points[0].x, points[0].y, stroke.width / 2, 0, Math.PI * 2);
    canvasContext.fill();
  } else {
    canvasContext.beginPath();
    canvasContext.moveTo(points[0].x, points[0].y);
    for (let index = 1; index < points.length - 1; index += 1) {
      const midpointX = (points[index].x + points[index + 1].x) / 2;
      const midpointY = (points[index].y + points[index + 1].y) / 2;
      canvasContext.quadraticCurveTo(points[index].x, points[index].y, midpointX, midpointY);
    }
    const last = points[points.length - 1];
    canvasContext.lineTo(last.x, last.y);
    canvasContext.stroke();
  }
  canvasContext.restore();
}

function renderCanvas() {
  const rect = board.getBoundingClientRect();
  const pixelRatio = window.devicePixelRatio || 1;
  canvasContext.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  canvasContext.clearRect(0, 0, rect.width, rect.height);
  for (const [key, stroke] of canvasStrokes) {
    if (!canvasErased.has(key)) drawStroke(stroke);
  }
  if (activeStroke) drawStroke(activeStroke);
}

function scheduleCanvasRender() {
  if (canvasFrame !== undefined) return;
  canvasFrame = requestAnimationFrame(() => {
    canvasFrame = undefined;
    renderCanvas();
  });
}

function updateReadyState() {
  ready = textReady && canvasReady && presenceReady;
  editor.disabled = !ready || currentMode !== "write";
  board.setAttribute("aria-disabled", String(!ready || currentMode !== "draw"));
  updateHistoryButtons();
}

function updateHistoryButtons() {
  undoButton.disabled = !ready;
  redoButton.disabled = !ready;
}

function recordHistory(action) {
  undoStack.push(action);
  redoStack.length = 0;
  updateHistoryButtons();
}

function applyTextHistory(action) {
  const deletedNodes = action.deleteIds
    .map((id) => nodes.get(idKey(id)))
    .filter((node) => node && !node.deleted);

  for (const node of deletedNodes) {
    sendOperation({ type: "delete", operation_id: nextId(), target: node.id });
  }

  const insertedNodes = [];
  let anchor = action.after;
  for (const value of action.insertChars) {
    const elementId = nextId();
    sendOperation({ type: "insert", element_id: elementId, after: anchor, value });
    insertedNodes.push({ id: elementId, value });
    anchor = elementId;
  }

  renderDocument();
  return {
    kind: "text",
    insertChars: deletedNodes.map((node) => node.value),
    deleteIds: insertedNodes.map((node) => node.id),
    after: action.after,
  };
}

function applyCanvasHistory(action) {
  if (action.kind === "canvas-remove") {
    const strokes = action.strokeIds
      .map((id) => canvasStrokes.get(idKey(id)))
      .filter((stroke) => stroke && !canvasErased.has(idKey(stroke.operation_id)));
    for (const stroke of strokes) {
      sendCanvasOperation({
        type: "canvas_erase",
        operation_id: nextId(),
        target: stroke.operation_id,
      });
    }
    return { kind: "canvas-restore", strokes };
  }

  if (action.kind === "canvas-restore") {
    if (!action.strokes.length) return undefined;
    const strokes = action.strokes.map(({ type, ...stroke }) => stroke);
    sendCanvasOperation({
      type: "canvas_restore",
      operation_id: nextId(),
      strokes,
    });
    return {
      kind: "canvas-remove",
      strokeIds: action.strokes.map((stroke) => stroke.operation_id),
    };
  }

  return undefined;
}

function applyHistoryAction(action) {
  return action.kind === "text" ? applyTextHistory(action) : applyCanvasHistory(action);
}

function undo() {
  const action = undoStack.pop();
  if (!action) return;
  const reverse = applyHistoryAction(action);
  if (reverse) redoStack.push(reverse);
  updateHistoryButtons();
}

function redo() {
  const action = redoStack.pop();
  if (!action) return;
  const reverse = applyHistoryAction(action);
  if (reverse) undoStack.push(reverse);
  updateHistoryButtons();
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
    renderDocument();
    textReady = true;
    updateReadyState();
    setConnectionState("connected", "Connected");
  } else if (message.type === "canvas_sync") {
    canvasStrokes.clear();
    canvasSeen.clear();
    canvasPending.clear();
    localCanvasOperations.clear();
    lastCanvasSequence = 0;
    for (const entry of message.operations) {
      receiveCanvasOperation(entry.seq, entry.operation);
    }
    canvasReady = true;
    updateReadyState();
    scheduleCanvasRender();
  } else if (message.type === "presence_snapshot") {
    users.clear();
    for (const user of message.users) users.add(user);
    renderUsers();
    presenceReady = true;
    updateReadyState();
  } else if (message.type === "operation") {
    applyOperation(message.operation);
    receiveSequence(message.seq);
    renderDocument();
  } else if (message.type === "ack") {
    receiveSequence(message.seq);
  } else if (message.type === "canvas_operation") {
    receiveCanvasOperation(message.seq, message.operation);
  } else if (message.type === "canvas_ack") {
    const operationKey = idKey(message.operation_id);
    const operation = localCanvasOperations.get(operationKey);
    if (operation) receiveCanvasOperation(message.seq, operation);
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
  textReady = false;
  canvasReady = false;
  presenceReady = false;
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
    textReady = false;
    canvasReady = false;
    presenceReady = false;
    updateReadyState();
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

function sendCanvasOperation(operation) {
  const operationKey = idKey(operation.operation_id);
  localCanvasOperations.set(operationKey, operation);
  applyCanvasOperation(operation);
  scheduleCanvasRender();
  send(operation);
}

function canvasPoint(event) {
  const rect = board.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
    y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
  };
}

function beginStroke(event) {
  if (!ready || currentMode !== "draw" || socket?.readyState !== WebSocket.OPEN) return;
  event.preventDefault();
  board.setPointerCapture(event.pointerId);
  const erasing = selectedTool === "eraser";
  activeStroke = {
    type: "stroke",
    operation_id: null,
    color: erasing ? "#fbfbf7" : selectedColor,
    width: erasing ? Math.max(strokeWidth * 3, 18) : strokeWidth,
    opacity: selectedTool === "marker" ? 0.34 : 1,
    points: [canvasPoint(event)],
  };
  scheduleCanvasRender();
}

function continueStroke(event) {
  if (!activeStroke) return;
  event.preventDefault();
  activeStroke.points.push(canvasPoint(event));
  scheduleCanvasRender();
}

function finishStroke() {
  if (!activeStroke) return;
  const operation = { ...activeStroke, operation_id: nextId() };
  activeStroke = undefined;
  sendCanvasOperation(operation);
  recordHistory({ kind: "canvas-remove", strokeIds: [operation.operation_id] });
}

function selectMode(mode) {
  currentMode = mode;
  appShell.dataset.mode = mode;
  drawModeButton.setAttribute("aria-pressed", String(mode === "draw"));
  writeModeButton.setAttribute("aria-pressed", String(mode === "write"));
  updateReadyState();
  if (mode === "write" && ready) editor.focus();
  if (mode === "draw") board.focus({ preventScroll: true });
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

  const deletedNodes = previous.slice(prefix, before.length - suffix);
  const insertionAnchor = prefix > 0 ? previous[prefix - 1].id : null;
  for (const node of deletedNodes) {
    sendOperation({ type: "delete", operation_id: nextId(), target: node.id });
  }
  const insertedNodes = [];
  let anchor = insertionAnchor;
  for (const value of after.slice(prefix, after.length - suffix)) {
    const elementId = nextId();
    sendOperation({ type: "insert", element_id: elementId, after: anchor, value });
    insertedNodes.push({ id: elementId, value });
    anchor = elementId;
  }
  if (deletedNodes.length || insertedNodes.length) {
    recordHistory({
      kind: "text",
      insertChars: deletedNodes.map((node) => node.value),
      deleteIds: insertedNodes.map((node) => node.id),
      after: insertionAnchor,
    });
  }
  send({ type: "presence", event: "cursor", value: editor.selectionStart });
  send({ type: "presence", event: "typing", value: true });
  clearTimeout(typingTimer);
  typingTimer = setTimeout(() => send({ type: "presence", event: "typing", value: false }), 900);
}

connectButton.addEventListener("click", () => {
  clearTimeout(reconnectTimer);
  const room = roomInput.value.trim() || "demo";
  if (socket) {
    intentionalClose.add(socket);
    socket.close();
  }
  if (room !== activeRoom) {
    nodes.clear(); children.clear(); children.set(rootKey, []);
    seenOperations.clear(); pendingOperations.clear();
    canvasStrokes.clear(); canvasSeen.clear(); canvasErased.clear(); canvasPending.clear();
    localCanvasOperations.clear();
    users.clear(); renderUsers();
    undoStack.length = 0;
    redoStack.length = 0;
    lastSequence = 0;
    lastCanvasSequence = 0;
    receivedSequences.clear();
    updateHistoryButtons();
    scheduleCanvasRender();
  }
  activeRoom = room;
  openSocket();
});

drawModeButton.addEventListener("click", () => selectMode("draw"));
writeModeButton.addEventListener("click", () => selectMode("write"));

toolButtons.forEach((button) => {
  button.addEventListener("click", () => {
    selectedTool = button.dataset.tool;
    toolButtons.forEach((toolButton) => {
      toolButton.setAttribute("aria-pressed", String(toolButton === button));
    });
    board.dataset.tool = selectedTool;
  });
});

swatches.forEach((swatch) => {
  swatch.addEventListener("click", () => {
    selectedColor = swatch.dataset.color;
    swatches.forEach((colorButton) => {
      colorButton.setAttribute("aria-pressed", String(colorButton === swatch));
    });
    if (selectedTool === "eraser") {
      selectedTool = "pen";
      toolButtons.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.tool === "pen")));
      board.dataset.tool = selectedTool;
    }
  });
});

widthInput.addEventListener("input", () => {
  strokeWidth = Number(widthInput.value);
  widthOutput.value = String(strokeWidth);
});

document.querySelector("#clear-canvas").addEventListener("click", () => {
  if (!ready || currentMode !== "draw") return;
  const strokes = [...canvasStrokes.values()].filter(
    (stroke) => !canvasErased.has(idKey(stroke.operation_id)),
  );
  if (!strokes.length) return;
  sendCanvasOperation({ type: "canvas_clear", operation_id: nextId() });
  recordHistory({ kind: "canvas-restore", strokes });
});

undoButton.addEventListener("click", undo);
redoButton.addEventListener("click", redo);
window.addEventListener("keydown", (event) => {
  if (!(event.ctrlKey || event.metaKey)) return;
  const key = event.key.toLowerCase();
  if (key !== "z" && key !== "y") return;
  event.preventDefault();
  if (key === "y" || event.shiftKey) redo();
  else undo();
});

board.addEventListener("pointerdown", beginStroke);
board.addEventListener("pointermove", continueStroke);
board.addEventListener("pointerup", finishStroke);
board.addEventListener("pointercancel", finishStroke);
new ResizeObserver(resizeCanvas).observe(board);
window.addEventListener("resize", resizeCanvas);

editor.addEventListener("input", handleInput);
editor.addEventListener("keyup", () => send({ type: "presence", event: "cursor", value: editor.selectionStart }));
editor.addEventListener("click", () => send({ type: "presence", event: "cursor", value: editor.selectionStart }));
window.addEventListener("beforeunload", () => { if (socket) intentionalClose.add(socket); socket?.close(); });
setInterval(() => send({ type: "ping" }), 20000);
activeRoom = roomInput.value;
resizeCanvas();
openSocket();
