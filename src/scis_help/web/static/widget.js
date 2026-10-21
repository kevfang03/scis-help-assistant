/*
 * Help Assistant chat widget (unofficial student project).
 *
 * Embed with one tag:
 *   <script src="http://127.0.0.1:8765/widget.js" data-mode="public" defer></script>
 * The API base URL is taken from this script's own src. Plain JavaScript, no framework, no
 * build step and no requests anywhere except that base URL. Everything renders inside a
 * Shadow DOM so the host page's styles can't reach it.
 */
(() => {
  "use strict";

  const script =
    document.currentScript ||
    Array.from(document.querySelectorAll('script[src*="widget.js"]')).pop();
  if (!script || window.__helpAssistantWidget) return;
  window.__helpAssistantWidget = true;

  const BASE = new URL(".", script.src);
  const MODE = script.dataset.mode === "desk" ? "desk" : "public";
  const STORE_KEY = "help-assistant-session-" + MODE;
  const MAX_CHARS = 500;
  const SVG_NS = "http://www.w3.org/2000/svg";

  // ---- small helpers -------------------------------------------------------------------

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "text") node.textContent = value;
      else if (key === "class") node.className = value;
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children || []) if (child) node.append(child);
    return node;
  }

  function svgIcon(pathData) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", pathData);
    svg.append(path);
    return svg;
  }

  function newSessionId() {
    if (window.crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16)); // non-secure contexts
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20),
      hex.slice(20)].join("-");
  }

  function loadSessionId() {
    try {
      const saved = sessionStorage.getItem(STORE_KEY);
      if (saved) return saved;
    } catch (_) { /* storage blocked: keep the id in memory only */ }
    return saveSessionId(newSessionId());
  }

  function saveSessionId(id) {
    try {
      sessionStorage.setItem(STORE_KEY, id);
    } catch (_) { /* storage blocked */ }
    return id;
  }

  // The server already sanitises answer_html; this copies only the same allowed elements and
  // attributes again, so nothing else can reach the page even if the server got it wrong.
  const ALLOWED = new Set(["P", "UL", "OL", "LI", "STRONG", "EM", "CODE", "PRE", "A", "BR", "SUP"]);

  function safeFragment(html) {
    const template = document.createElement("template");
    template.innerHTML = html; // inert: template content never runs scripts or loads images
    const out = document.createDocumentFragment();
    copyChildren(template.content, out);
    return out;
  }

  function copyChildren(from, to) {
    for (const node of from.childNodes) {
      if (node.nodeType === Node.TEXT_NODE) {
        to.append(document.createTextNode(node.textContent));
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        if (!ALLOWED.has(node.tagName)) {
          copyChildren(node, to);
          continue;
        }
        const copy = document.createElement(node.tagName.toLowerCase());
        if (node.tagName === "A") {
          const href = node.getAttribute("href") || "";
          if (!/^https?:\/\//i.test(href)) {
            copyChildren(node, to);
            continue;
          }
          copy.href = href;
          copy.target = "_blank";
          copy.rel = "noopener noreferrer";
        }
        if (node.tagName === "OL" && /^\d+$/.test(node.getAttribute("start") || "")) {
          copy.setAttribute("start", node.getAttribute("start"));
        }
        copyChildren(node, copy);
        to.append(copy);
      }
    }
  }

  function sourceLink(source) {
    const label = source.heading ? source.title + " › " + source.heading : source.title;
    return el("a", {
      href: source.url, target: "_blank", rel: "noopener noreferrer", text: label,
    });
  }

  // ---- DOM -----------------------------------------------------------------------------

  const host = el("div", { id: "help-assistant-widget" });
  const shadow = host.attachShadow({ mode: "open" });
  const root = el("div", { class: "root", hidden: true });
  const stylesheet = el("link", { rel: "stylesheet", href: new URL("widget.css", BASE).href });
  stylesheet.addEventListener("load", () => { root.hidden = false; });
  stylesheet.addEventListener("error", () => { root.hidden = false; });

  const bubble = el("button", {
    class: "bubble", type: "button", "aria-label": "Open help chat", "aria-expanded": "false",
    "aria-controls": "panel",
  }, [svgIcon("M4 4h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-5 4v-4H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z")]);

  const title = el("h2", { id: "title", class: "title", text: "Help Assistant (unofficial)" });
  const newChatButton = el("button", { class: "text-button", type: "button", text: "New chat",
    "aria-label": "Start a new chat" });
  const closeButton = el("button", { class: "icon-button", type: "button",
    "aria-label": "Close help chat" }, [svgIcon("M6 6l12 12M18 6L6 18")]);
  const messages = el("div", { class: "messages" });
  const live = el("div", { class: "visually-hidden", "aria-live": "polite", "aria-atomic": "true" });
  const input = el("textarea", {
    class: "input", rows: "2", maxlength: String(MAX_CHARS), "aria-label": "Your question",
    placeholder: "Describe your problem…",
  });
  const sendButton = el("button", { class: "send", type: "submit", text: "Send" });
  const form = el("form", { class: "composer" }, [input, sendButton]);

  const panel = el("div", {
    id: "panel", class: "panel", role: "dialog", "aria-labelledby": "title", hidden: true,
  }, [
    el("div", { class: "header" }, [
      el("div", { class: "heading" }, [
        title,
        MODE === "desk" ? el("span", { class: "badge", text: "Desk mode" }) : null,
      ]),
      newChatButton,
      closeButton,
    ]),
    el("p", { class: "privacy", text: "Don't enter passwords or student IDs." }),
    messages,
    live,
    form,
    el("p", {
      class: "footer",
      text: "Unofficial student project · answers come from public SCIS support pages",
    }),
  ]);

  root.append(bubble, panel);
  shadow.append(stylesheet, root);

  // ---- behaviour -----------------------------------------------------------------------

  let sessionId = loadSessionId();
  let busy = false;

  function scrollDown() {
    messages.scrollTop = messages.scrollHeight;
  }

  function addMessage(kind, children) {
    const node = el("div", { class: "msg " + kind }, children);
    messages.append(node);
    scrollDown();
    return node;
  }

  function welcome() {
    const text = MODE === "desk"
      ? "Desk mode: more technical detail and every retrieved page. Describe the problem."
      : "Hi! Describe your problem in your own words and I'll look for the right support page.";
    addMessage("bot", [el("p", { text })]);
  }

  function announce(text) {
    live.textContent = "";
    window.setTimeout(() => { live.textContent = text; }, 50);
  }

  function feedbackRow(answerId) {
    const status = el("span", { class: "feedback-status" });
    const up = el("button", { class: "vote", type: "button", "aria-label": "Yes, this helped",
      text: "👍" });
    const down = el("button", { class: "vote", type: "button",
      "aria-label": "No, this didn't help", text: "👎" });
    const forSession = sessionId;
    async function vote(helpful) {
      up.disabled = true;
      down.disabled = true;
      (helpful ? up : down).setAttribute("aria-pressed", "true");
      try {
        const response = await fetch(new URL("api/feedback", BASE), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "omit",
          body: JSON.stringify({ session_id: forSession, answer_id: answerId, helpful }),
        });
        status.textContent = response.ok ? "Thanks!" : "Sorry, that didn't save.";
      } catch (_) {
        status.textContent = "Sorry, that didn't save.";
      }
    }
    up.addEventListener("click", () => vote(true));
    down.addEventListener("click", () => vote(false));
    return el("div", { class: "feedback" }, [
      el("span", { text: "Did this help?" }), up, down, status,
    ]);
  }

  function showAnswer(data) {
    const parts = [];
    if (data.scrub_notice) {
      parts.push(el("p", { class: "notice",
        text: "I removed something that looked like a password or ID. Please don't share those." }));
    }
    const body = el("div", { class: "answer" });
    body.append(safeFragment(data.answer_html || ""));
    parts.push(body);

    const sources = data.sources || [];
    const used = sources.filter((s) => s.used && s.n > 0).sort((a, b) => a.n - b.n);
    const also = sources.filter((s) => !s.used);
    if (used.length) {
      const list = el("ol", { class: "sources" });
      for (const source of used) {
        list.append(el("li", { value: String(source.n) }, [sourceLink(source)]));
      }
      parts.push(el("p", { class: "sources-label", text: "Sources" }), list);
    }
    if (MODE === "desk" && also.length) {
      const list = el("ul", { class: "sources" });
      for (const source of also) list.append(el("li", {}, [sourceLink(source)]));
      parts.push(el("details", { class: "also" }, [
        el("summary", { text: "Also retrieved (" + also.length + ")" }), list,
      ]));
    }
    if (data.notice) parts.push(el("p", { class: "notice", text: data.notice }));
    if (data.status === "answered" && data.answer_id) parts.push(feedbackRow(data.answer_id));
    addMessage("bot", parts);
    announce(body.textContent || "New answer");
  }

  function showError(text) {
    addMessage("bot error", [el("p", { text })]);
    announce(text);
  }

  function setBusy(value) {
    busy = value;
    input.disabled = value;
    sendButton.disabled = value;
  }

  async function send() {
    const message = input.value.trim();
    if (busy || !message) return;
    if (message.length > MAX_CHARS) {
      showError("Please keep your message under " + MAX_CHARS + " characters.");
      return;
    }
    addMessage("user", [el("p", { text: message })]);
    input.value = "";
    setBusy(true);
    const thinking = addMessage("bot thinking", [el("p", { text: "Thinking…" })]);
    try {
      const response = await fetch(new URL("api/chat", BASE), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "omit",
        body: JSON.stringify({ session_id: sessionId, message, mode: MODE }),
      });
      thinking.remove();
      let data = null;
      try { data = await response.json(); } catch (_) { data = null; }
      if (response.ok && data) {
        showAnswer(data);
      } else if (response.status === 429) {
        const wait = response.headers.get("Retry-After");
        showError((data && data.detail) ||
          "You're sending messages too fast. Please wait " + (wait || "a few") + " seconds.");
      } else {
        showError((data && data.detail) || "Something went wrong. Please try again.");
      }
    } catch (_) {
      thinking.remove();
      showError("Can't reach the help assistant right now. Please try again in a moment.");
    } finally {
      setBusy(false);
      input.focus();
    }
  }

  function open() {
    panel.hidden = false;
    bubble.setAttribute("aria-expanded", "true");
    bubble.setAttribute("aria-label", "Close help chat");
    input.focus();
    scrollDown();
  }

  function close() {
    panel.hidden = true;
    bubble.setAttribute("aria-expanded", "false");
    bubble.setAttribute("aria-label", "Open help chat");
    bubble.focus();
  }

  function newChat() {
    sessionId = saveSessionId(newSessionId());
    messages.replaceChildren();
    welcome();
    announce("New chat started");
    input.value = "";
    input.focus();
  }

  bubble.addEventListener("click", () => (panel.hidden ? open() : close()));
  closeButton.addEventListener("click", close);
  newChatButton.addEventListener("click", newChat);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    send();
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send();
    }
  });
  root.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.hidden) {
      event.preventDefault();
      close();
    }
  });

  welcome();
  document.body.append(host);
})();
