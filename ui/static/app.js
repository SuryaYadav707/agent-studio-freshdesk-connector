// Demo UI behaviour: health checks, schema-driven tool forms, streaming chat with a tool timeline.
(function () {
  const $ = (s) => document.querySelector(s);
  const pretty = (o) => JSON.stringify(o, null, 2);

  // ---------------------------------------------------------- connections
  document.querySelectorAll("[data-health]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const route = btn.dataset.health;
      const out = $("#health-" + route);
      out.hidden = false;
      out.textContent = "checking…";
      try {
        const res = await fetch("/health/" + route);
        out.textContent = pretty(await res.json());
      } catch (e) {
        out.textContent = "request failed: " + e;
      }
    });
  });

  // ------------------------------------------------------------ playground
  const toolsJson = $("#tools-json");
  if (toolsJson) {
    const tools = JSON.parse(toolsJson.textContent);
    const byName = Object.fromEntries(tools.map((t) => [t.name, t]));
    let current = null;

    // JSON Schema shapes we see: {type, enum}, {anyOf:[{type|enum}, {type:"null"}]}, arrays with items.enum
    const typesOf = (prop) => [].concat(prop.type || (prop.anyOf || []).map((a) => a.type)).filter(Boolean);
    const extractEnumValues = (prop) => prop.enum || (prop.anyOf || []).flatMap((a) => a.enum || []);
    const extractItemEnum = (prop) => (prop.items && prop.items.enum) || (prop.anyOf || []).flatMap((a) => (a.items && a.items.enum) || []);

    function fieldFor(name, prop, required) {
      const wrap = document.createElement("label");
      const types = typesOf(prop);
      const isArray = types.includes("array");
      const isBool = types.includes("boolean");
      const isNum = types.includes("integer") || types.includes("number");
      const enumVals = extractEnumValues(prop);
      const itemEnum = extractItemEnum(prop);
      wrap.append(`${name}${required ? " *" : ""} ${prop.description ? "— " + prop.description : ""}`);
      let input;
      if (isBool) {
        input = document.createElement("select");
        [["", "(default)"], ["true", "true"], ["false", "false"]].forEach(([v, l]) => {
          const o = document.createElement("option"); o.value = v; o.textContent = l; input.append(o);
        });
      } else if (isArray && itemEnum.length) {
        input = document.createElement("select"); input.multiple = true; input.size = Math.min(itemEnum.length, 5);
        itemEnum.forEach((v) => { const o = document.createElement("option"); o.value = v; o.textContent = v; input.append(o); });
      } else if (!isArray && enumVals.length) {
        input = document.createElement("select");
        const blank = document.createElement("option"); blank.value = ""; blank.textContent = "(default)"; input.append(blank);
        enumVals.forEach((v) => { const o = document.createElement("option"); o.value = v; o.textContent = v; input.append(o); });
      } else {
        input = document.createElement("input");
        input.type = isNum ? "number" : "text";
        if (isArray) input.placeholder = "comma separated";
        if (prop.default !== undefined && prop.default !== null) input.placeholder = "default: " + prop.default;
      }
      input.name = name;
      input.dataset.kind = isBool ? "bool" : isArray ? "array" : isNum ? "num" : "str";
      wrap.append(input);
      return wrap;
    }

    function select(name) {
      current = byName[name];
      document.querySelectorAll("#toollist button").forEach((b) => b.classList.toggle("sel", b.dataset.tool === name));
      $("#tool-empty").hidden = true;
      $("#tool-panel").hidden = false;
      $("#tool-name").textContent = current.name;
      $("#tool-desc").textContent = current.description;
      const docs = $("#tool-docs");
      docs.href = (current.meta && current.meta.documentation_url) || "#";
      docs.hidden = !(current.meta && current.meta.documentation_url);
      $("#tool-schema").textContent = pretty(current.inputSchema);
      $("#tool-out").textContent = "—";
      $("#tool-timing").textContent = "";
      const form = $("#tool-form");
      form.innerHTML = "";
      const props = current.inputSchema.properties || {};
      const req = current.inputSchema.required || [];
      if (!Object.keys(props).length) form.append(Object.assign(document.createElement("p"), { className: "muted", textContent: "No inputs." }));
      Object.entries(props).forEach(([n, p]) => form.append(fieldFor(n, p, req.includes(n))));
    }

    function collect() {
      const out = {};
      $("#tool-form").querySelectorAll("[name]").forEach((el) => {
        const kind = el.dataset.kind;
        if (kind === "bool") { if (el.value) out[el.name] = el.value === "true"; return; }
        if (el.multiple) { const v = [...el.selectedOptions].map((o) => o.value); if (v.length) out[el.name] = v; return; }
        if (!el.value) return;
        if (kind === "array") out[el.name] = el.value.split(",").map((s) => s.trim()).filter(Boolean);
        else if (kind === "num") out[el.name] = Number(el.value);
        else out[el.name] = el.value;
      });
      return out;
    }

    document.querySelectorAll("#toollist button").forEach((b) => b.addEventListener("click", () => select(b.dataset.tool)));
    $("#tool-run").addEventListener("click", async (e) => {
      e.preventDefault();
      if (!current) return;
      const [route, task] = (() => { const i = current.name.indexOf("_", current.name.startsWith("zoho_") ? 5 : 0); return [current.name.slice(0, i), current.name.slice(i + 1)]; })();
      const out = $("#tool-out"); out.textContent = "running…";
      const t0 = performance.now();
      try {
        const res = await fetch(`/tasks/${route}/${task}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(collect()) });
        out.textContent = pretty(await res.json());
      } catch (err) { out.textContent = "request failed: " + err; }
      $("#tool-timing").textContent = Math.round(performance.now() - t0) + " ms";
    });
    if (tools.length) select(tools[0].name);
  }

  // ------------------------------------------------------------------ chat
  const chatForm = $("#chat-form");
  if (chatForm) {
    const log = $("#chat-log");
    const q = $("#chat-q");
    const sendBtn = $("#chat-send");
    const stopBtn = $("#chat-stop");
    let history = [];        // full transcript (user, assistant blocks, tool results) replayed to the model
    let controller = null;

    const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    // Model output and anything it echoes (ticket bodies, item names) is untrusted: always sanitize.
    const md = (text) => {
      if (!(window.marked && window.DOMPurify)) return esc(text).replace(/\n/g, "<br>");
      return DOMPurify.sanitize(marked.parse(text, { breaks: true }), { USE_PROFILES: { html: true } });
    };
    const el = (tag, cls, html) => { const d = document.createElement(tag); if (cls) d.className = cls; if (html !== undefined) d.innerHTML = html; return d; };
    const scroll = () => { log.lastElementChild && log.lastElementChild.scrollIntoView({ block: "end" }); };
    const fmtTime = (t) => new Date(t * 1000).toLocaleTimeString();
    const friendly = (name) => {
      const [, src, action] = name.match(/^(freshdesk|zoho_inventory)_(.+)$/) || [];
      const where = src === "zoho_inventory" ? "Zoho Inventory" : src === "freshdesk" ? "Freshdesk" : name;
      return `Checked ${where}: ${(action || name).replace(/_/g, " ")}`;
    };
    const rateLimitNote = (h) => {
      const rem = h["x-rate-limit-remaining"] ?? h["x-ratelimit-remaining"];
      const lim = h["x-rate-limit-limit"] ?? h["x-ratelimit-total"];
      return rem !== undefined ? `rate limit ${rem}${lim ? "/" + lim : ""} left` : "";
    };
    const devToggle = $("#dev-toggle");
    const applyView = () => document.body.classList.toggle("merchant", !devToggle.checked);
    devToggle.addEventListener("change", applyView); applyView();

    function addUser(text) { log.append(el("div", "msg user", esc(text))); scroll(); }

    function startTurn() {
      const wrap = el("div", "turn");
      const timeline = el("div", "timeline"); timeline.hidden = true;
      const bubble = el("div", "msg assistant streaming", "<span class='cursor'></span>");
      const meta = el("div", "meta-line muted small"); meta.hidden = true;
      wrap.append(timeline, bubble, meta); log.append(wrap); scroll();
      return { wrap, timeline, bubble, meta, text: "", tools: {} };
    }

    function toolCard(turn, ev) {
      turn.timeline.hidden = false;
      const card = el("details", "tool pending");
      card.innerHTML = `<summary><span class="status">⏳</span> <span class="friendly">${esc(friendly(ev.name))}</span><code class="dev-only">${esc(ev.name)}</code> <span class="args muted small"></span> <span class="right muted small"></span></summary>`;
      const body = el("div", "tool-body");
      body.innerHTML = `<div class="muted small">arguments</div><pre class="out args-pre">{}</pre><div class="muted small">result</div><pre class="out result-pre">…</pre>`;
      card.append(body);
      turn.timeline.append(card);
      turn.tools[ev.id] = { card, argsText: "" };
      scroll();
    }

    function onEvent(turn, ev) {
      switch (ev.event) {
        case "text":
          turn.text += ev.delta;
          turn.bubble.innerHTML = md(turn.text) + "<span class='cursor'></span>";
          scroll();
          break;
        case "tool_start":
          toolCard(turn, ev);
          break;
        case "tool_input": {
          const last = Object.values(turn.tools).at(-1);
          if (!last) break;
          last.argsText += ev.delta;
          last.card.querySelector(".args-pre").textContent = last.argsText;
          last.card.querySelector(".args").textContent = last.argsText.slice(0, 90);
          break;
        }
        case "tool_result": {
          const t = turn.tools[ev.id];
          if (!t) break;
          t.card.classList.remove("pending");
          t.card.classList.add(ev.ok ? "ok" : "bad");
          t.card.querySelector(".status").textContent = ev.ok ? "✓" : "✗";
          t.card.querySelector(".args").textContent = JSON.stringify(ev.input);
          const rl = ev.output && ev.output.rate_limit ? rateLimitNote(ev.output.rate_limit) : "";
          t.card.querySelector(".right").innerHTML = `${esc(ev.summary)} <span class="lat">· ${ev.ms} ms${rl ? " · " + esc(rl) : ""}</span> · ${fmtTime(ev.ts)}`;
          t.card.querySelector(".args-pre").textContent = pretty(ev.input);
          t.card.querySelector(".result-pre").textContent = pretty(ev.output);
          break;
        }
        case "done": {
          turn.transcript = ev.transcript || [];
          const u = ev.usage || {};
          turn.meta.hidden = false; turn.meta.classList.add("dev-only");
          turn.meta.textContent = `${ev.model} · ${ev.rounds} round${ev.rounds === 1 ? "" : "s"} · ${Object.keys(turn.tools).length} tool call${Object.keys(turn.tools).length === 1 ? "" : "s"} · in ${u.input_tokens || 0} / out ${u.output_tokens || 0} tokens` +
            (u.cache_read_input_tokens ? ` (${u.cache_read_input_tokens} cached)` : "") + ` · ≈ $${(ev.cost_usd || 0).toFixed(4)}`;
          break;
        }
        case "notice":
          turn.timeline.hidden = false;
          turn.timeline.append(el("div", "muted small", esc("ℹ " + ev.message)));
          break;
        case "error":
          turn.bubble.classList.add("error");
          turn.text += (turn.text ? "\n\n" : "") + "⚠ " + ev.message;
          turn.bubble.innerHTML = md(turn.text);
          break;
      }
    }

    async function ask(question) {
      question = (question || "").trim();
      if (!question || controller) return;
      addUser(question);
      q.value = ""; q.disabled = true; sendBtn.disabled = true; stopBtn.hidden = false;
      const turn = startTurn();
      controller = new AbortController();
      try {
        const res = await fetch("/chat/stream", {
          method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ question, history }), signal: controller.signal,
        });
        if (!res.ok || !res.body) throw new Error("HTTP " + res.status);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let idx;
          while ((idx = buf.indexOf("\n\n")) >= 0) {
            const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
            const line = chunk.split("\n").find((l) => l.startsWith("data: "));
            if (line) onEvent(turn, JSON.parse(line.slice(6)));
          }
        }
      } catch (e) {
        if (e.name === "AbortError") { turn.text += (turn.text ? "\n\n" : "") + "_Stopped by user._"; turn.bubble.innerHTML = md(turn.text); }
        else onEvent(turn, { event: "error", message: "request failed: " + e.message });
      } finally {
        turn.bubble.classList.remove("streaming");
        turn.bubble.innerHTML = md(turn.text || "(no answer)");
        if (turn.transcript && !turn.bubble.classList.contains("error")) {
          history = history.concat(turn.transcript);
        }
        controller = null; q.disabled = false; sendBtn.disabled = false; stopBtn.hidden = true; q.focus(); scroll();
      }
    }

    chatForm.addEventListener("submit", (e) => { e.preventDefault(); ask(q.value); });
    stopBtn.addEventListener("click", () => controller && controller.abort());
    $("#chat-new").addEventListener("click", () => { history = []; log.innerHTML = ""; q.focus(); });
    document.querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => ask(b.dataset.q)));
  }
})();
