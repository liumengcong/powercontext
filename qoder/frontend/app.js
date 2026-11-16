/* StudyFlow 前端：只负责展示与交互，事实来源始终是后端 PowerContext 返回的数据。 */

const KIND_LABELS = {
  note: "笔记",
  preference: "偏好",
  weakness: "薄弱点",
  progress: "进度",
  task: "待办",
  task_status: "状态事件",
};
const STATE_LABELS = { active: "在用", forgotten: "已遗忘", merged: "已合并", retired: "已退役", todo: "待办", done: "已完成" };

const el = (tag, attrs = {}, children = []) => {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([key, value]) => {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined) node.setAttribute(key, value);
  });
  (Array.isArray(children) ? children : [children]).forEach((child) => {
    if (child === null || child === undefined) return;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  });
  return node;
};

const withQuery = (path, params) => {
  const entries = Object.entries(params || {}).filter(([, value]) => value !== undefined && value !== null);
  if (!entries.length) return path;
  return `${path}?${new URLSearchParams(entries.map(([key, value]) => [key, String(value)])).toString()}`;
};

const api = {
  async call(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    let payload = null;
    const text = await response.text();
    try {
      payload = text ? JSON.parse(text) : null;
    } catch (error) {
      payload = { raw: text };
    }
    if (!response.ok) {
      let detail = payload && payload.detail !== undefined ? payload.detail : payload;
      if (detail && typeof detail === "object" && detail.error) detail = detail.error;
      const message =
        typeof detail === "string" ? detail : (detail && (detail.message || detail.code)) || `请求失败（${response.status}）`;
      const error = new Error(message);
      error.status = response.status;
      error.detail = detail && typeof detail === "object" ? detail : { message: String(detail) };
      throw error;
    }
    return payload;
  },
  get: (path, params) => api.call(withQuery(path, params)),
  post: (path, body) => api.call(path, { method: "POST", body: JSON.stringify(body ?? {}) }),
  // 后端 DELETE 用查询参数做 PowerContext 的 compare-and-set，不发送 JSON 请求体。
  del: (path, params) => api.call(withQuery(path, params), { method: "DELETE" }),
};

const state = {
  status: null,
  view: "overview",
  memories: [],
  statesParam: "active",
  kindParam: "",
  listMeta: null,
  continuation: null,
  searchQuery: "",
  searchResult: null,
  tasks: null,
  log: [],
  busy: false,
};

const view = document.getElementById("view");
const conn = document.getElementById("conn");
const connText = document.getElementById("conn-text");
const banner = document.getElementById("banner");
const scopeLine = document.getElementById("scope-line");
const workspaceSelect = document.getElementById("workspace-select");

function toast(message, kind = "") {
  const node = document.getElementById("toast");
  node.textContent = message;
  node.className = `toast ${kind}`;
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    node.hidden = true;
  }, 4200);
}

function showBanner(text, kind = "") {
  if (!text) {
    banner.hidden = true;
    return;
  }
  banner.hidden = false;
  banner.className = `banner ${kind}`;
  banner.textContent = text;
}

function setConnection(status) {
  conn.classList.remove("online", "offline", "checking");
  if (!status || !status.connected) {
    conn.classList.add("offline");
    connText.textContent = "PowerContext 已断开";
    scopeLine.textContent = "无法读写记忆：界面不会显示任何成功结果。";
    showBanner(
      `${status ? status.message : "PowerContext 未连接"} 请检查后端进程与数据目录后重新打开页面。`,
      "error"
    );
    return;
  }
  conn.classList.add("online");
  const pkg = status.powercontext_package;
  connText.textContent = `已连接 powercontext ${pkg}`;
  scopeLine.textContent = `工作区「${status.workspace}」· scope ${status.scope_id || "—"} · ${status.runtime_mode}`;
  showBanner(status.search_note);
  document.getElementById("pkg-tag").textContent = `powercontext ${pkg}`;
  document.getElementById("engine-tag").textContent = "规则整理，未调用模型";
}

function citationButton(citation) {
  if (!citation) return null;
  return el("button", {
    class: "link",
    onclick: () => openCitation(citation),
    text: "查看出处",
  });
}

function openCitation(citation) {
  const modal = document.getElementById("modal");
  const body = document.getElementById("modal-body");
  body.textContent = "";
  body.appendChild(el("p", { class: "mem-text", text: citation.quote || citation.text || "（无正文）" }));
  const rows = [
    ["类别", `${KIND_LABELS[citation.kind] || citation.kind}（kind=${citation.kind}）`],
    ["family", citation.family],
    ["artifact_id", citation.artifact_id],
    ["revision", String(citation.revision)],
    ["状态", `${STATE_LABELS[citation.state] || citation.state} · state_version ${citation.state_version}`],
    ["描述", citation.description || "—"],
  ];
  body.appendChild(
    el(
      "table",
      {},
      rows.map(([key, value]) => el("tr", {}, [el("th", { text: key }), el("td", { class: "mono", text: value })]))
    )
  );
  body.appendChild(
    el("p", {
      class: "hint",
      text: "以上字段直接来自 PowerContext atomic-memory artifact，不是页面推断。",
    })
  );
  document.getElementById("modal-title").textContent = "记忆出处";
  modal.hidden = false;
}

document.getElementById("modal-close").addEventListener("click", () => {
  document.getElementById("modal").hidden = true;
});
document.getElementById("modal").addEventListener("click", (event) => {
  if (event.target.id === "modal") event.target.hidden = true;
});

function forgetButton(record) {
  return el("button", {
    class: "link danger",
    text: "遗忘",
    onclick: () => forgetMemory(record),
  });
}

function memoryCard(record, extra = []) {
  const head = el("div", { class: "mem-head" }, [
    el("span", { class: "chip kind", "data-kind": record.kind, text: KIND_LABELS[record.kind] || record.kind }),
    el("span", { class: `chip state-${record.state}`, text: `${STATE_LABELS[record.state] || record.state}` }),
    el("span", { class: "chip", text: `rev ${record.revision}` }),
  ]);
  const forget = record.state === "active" && record.kind !== "task_status" ? forgetButton(record) : null;
  const foot = el("div", { class: "mem-foot" }, [
    citationButton(record.citation),
    el("span", { class: "mono", text: record.artifact_id }),
    ...extra,
    forget,
  ]);
  return el("div", { class: "mem", "data-kind": record.kind }, [
    head,
    el("p", { class: "mem-text", text: record.text }),
    record.description ? el("p", { class: "hint", text: record.description }) : null,
    foot,
  ]);
}

function loading(text = "读取中…") {
  view.textContent = "";
  view.appendChild(el("div", { class: "card", text }));
}

function emptyState(title, detail, action) {
  return el("div", { class: "empty wide" }, [
    el("strong", { text: title }),
    el("span", { text: detail }),
    action ? el("div", { style: "margin-top:12px" }, [action]) : null,
  ]);
}

/* --------------------------------------------------------------------- 视图 */

const views = {
  overview() {
    const counts = state.status.counts || {};
    const cards = [];
    cards.push(
      el("div", { class: "card" }, [
        el("h2", { text: "本次要继续" }),
        state.continuation && state.continuation.next_steps.length
          ? el(
              "ol",
              { class: "steps" },
              state.continuation.next_steps.map((step) =>
                el("li", {}, [
                  el("div", { text: step.text }),
                  el("div", { class: "mem-foot" }, [citationButton(step.citation), el("span", { class: "hint", text: step.reason })]),
                ])
              )
            )
          : el("p", { class: "hint", text: "还没有可读回的待办。导入演示数据或新建一条记忆后刷新本页。" }),
      ])
    );
    // 状态事件是应用内部记录，不放进“最近学习记录”；记忆库仍可筛选查看。
    const recent = state.memories.filter((item) => item.state === "active" && item.kind !== "task_status").slice(-3).reverse();
    cards.push(
      el("div", { class: "card" }, [
        el("h2", { text: "最近学习记录" }),
        recent.length
          ? el("ul", { class: "plain" }, recent.map((item) => memoryCard(item)))
          : el("p", { class: "hint", text: "当前工作区没有 active 的笔记/偏好/薄弱点/进度/待办记录。" }),
      ])
    );
    const weaknesses = state.memories.filter((item) => item.kind === "weakness" && item.state === "active");
    cards.push(
      el("div", { class: "card" }, [
        el("h2", { text: "仍待解决的薄弱点" }),
        weaknesses.length
          ? el("ul", { class: "plain" }, weaknesses.map((item) => memoryCard(item)))
          : el("p", { class: "hint", text: "没有 kind=weakness 的记录，因此无法给出薄弱点建议。" }),
      ])
    );
    const stats = el("div", { class: "card" }, [
      el("h2", { text: "PowerContext 真实读数" }),
      el("div", { class: "stat-grid" }, [
        el("div", { class: "stat" }, [el("b", { text: String(counts.active ?? "—") }), el("span", { text: "active 记忆" })]),
        el("div", { class: "stat" }, [el("b", { text: String(counts.total ?? "—") }), el("span", { text: "含历史状态总数" })]),
        el("div", { class: "stat" }, [
          el("b", { text: String((state.tasks || []).filter((t) => t.status === "todo").length) }),
          el("span", { text: "未完成待办" }),
        ]),
        el("div", { class: "stat" }, [
          el("b", { text: String((state.tasks || []).filter((t) => t.status === "done").length) }),
          el("span", { text: "已完成待办" }),
        ]),
      ]),
      el("p", {
        class: "hint",
        text: "上面的数字来自本次进程对 PowerContext 的实际读取；关掉后端再打开会重新读取，不使用页面缓存。",
      }),
    ]);
    if (!state.memories.length) {
      return [
        el("div", { class: "grid" }, [
          emptyState(
            "这个工作区还是空的",
            "研习台只保存你明确确认过的信息。可以先导入合成演示数据看看完整流程，演示数据写入独立演示 scope，不会混入这里。",
            el("button", { class: "accent", text: "导入演示数据", onclick: () => importDemo() })
          ),
        ]),
      ];
    }
    return [el("div", { class: "grid" }, [...cards, stats])];
  },

  memories() {
    const kindOptions = ["", ...Object.keys(KIND_LABELS).filter((k) => k !== "task_status")];
    const form = el("form", {
      class: "card",
      onsubmit: (event) => {
        event.preventDefault();
        createMemory(event.target);
      },
    }, [
      el("h3", { text: "新建记忆（显式保存，不经过模型）" }),
      el("div", { class: "row" }, [
        (() => {
          const select = el("select", { name: "kind" }, kindOptions.filter(Boolean).map((kind) => el("option", { value: kind, text: KIND_LABELS[kind] })));
          return el("label", {}, [el("span", { class: "hint", text: "类别" }), select]);
        })(),
        (() => el("label", {}, [el("span", { class: "hint", text: "正文" }), el("textarea", { name: "text", rows: "3", placeholder: "写下要长期保留的一条事实、偏好或进度…" })]))(),
        (() => el("label", {}, [el("span", { class: "hint", text: "一行摘要（可选）" }), el("input", { name: "description", maxlength: "200" })]))(),
        el("button", { class: "accent", type: "submit", text: "保存" }),
      ]),
      el("p", { class: "hint", text: "正文最长 8192 UTF-8 字节；保存后立即写入 PowerContext atomic-memory，并返回真实 artifact_id 与 revision。" }),
    ]);

    const statesSelect = el("select", { id: "states-filter", onchange: (event) => loadMemories(event.target.value, state.kindParam) }, [
      el("option", { value: "active", text: "仅 active" }),
      el("option", { value: "all", text: "包含已遗忘/已合并" }),
    ]);
    statesSelect.value = state.statesParam;
    const kindSelect = el("select", { id: "kind-filter", onchange: (event) => loadMemories(state.statesParam, event.target.value) }, [
      el("option", { value: "", text: "全部类别" }),
      ...Object.keys(KIND_LABELS).map((kind) => el("option", { value: kind, text: `${KIND_LABELS[kind]}（kind=${kind}）` })),
    ]);
    kindSelect.value = state.kindParam;

    const filter = el("div", { class: "card" }, [
      el("div", { class: "row" }, [
        el("label", {}, [el("span", { class: "hint", text: "状态范围" }), statesSelect]),
        el("label", {}, [el("span", { class: "hint", text: "类别" }), kindSelect]),
        el("p", { class: "hint", text: "状态事件 kind=task_status 是待办的追加记录，可在这里核对，不参与“最近学习记录”。" }),
      ]),
    ]);

    const meta = state.listMeta || {};
    const list = state.memories.length
      ? el("div", { class: "grid" }, [el("div", { class: "card wide" }, [el("h3", { text: `记忆库 · ${state.memories.length} 条` }), el("p", { class: "hint", text: meta.note || "" }), el("ul", { class: "plain" }, state.memories.map((item) => memoryCard(item)))])])
      : el("div", { class: "grid" }, [emptyState("没有记忆", "新建一条，或导入演示数据。", el("button", { class: "accent", text: "导入演示数据", onclick: () => importDemo() }))]);
    return [form, filter, list];
  },

  continue() {
    const payload = state.continuation;
    if (!payload) return [el("div", { class: "card", text: "继续学习清单尚未载入。" })];
    if (payload.empty) {
      return [
        el("div", { class: "grid" }, [
          emptyState(
            "没有可读回的记忆",
            "关闭应用后重新打开，这里会从 PowerContext 读回上次进度；现在还没有任何记录，因此不给任何建议。",
            el("button", { class: "accent", text: "导入演示数据", onclick: () => importDemo() })
          ),
        ]),
      ];
    }
    const pref = payload.preference;
    const controls = el("div", { class: "card" }, [
      el("div", { class: "row" }, [
        el("label", {}, [
          el("span", { class: "hint", text: "本次可用时长（分钟）" }),
          el("input", { id: "minutes", type: "number", min: "1", max: "480", value: String(pref.available_minutes) }),
        ]),
        el("button", { text: "按此时长重算", onclick: () => loadContinuation(document.getElementById("minutes").value) }),
        el("button", { class: "ghost", text: "回到记忆里的时长", onclick: () => loadContinuation("") }),
      ]),
      el("p", { class: "hint", text: `建议上限 ${pref.max_tasks} 项 · 优先薄弱点：${pref.prefer_weakness ? "是" : "否"} · 生成方式 ${pref.engine}` }),
      el("div", { class: "rules" }, [
        el("strong", { text: "本次使用的规则" }),
        el("ul", {}, pref.rules.map((rule) => el("li", { text: rule }))),
      ]),
    ]);

    const section = (title, items, render) =>
      el("div", { class: "card" }, [
        el("h2", { text: title }),
        items.length ? el("ul", { class: "plain" }, items.map((item) => render(item))) : el("p", { class: "hint", text: "没有相关记录，应用不会编造内容。" }),
      ]);

    const renderRef = (item) => el("li", { class: "mem" }, [el("p", { class: "mem-text", text: item.quote || item.text }), el("div", { class: "mem-foot" }, [citationButton(item), el("span", { class: "mono", text: `${item.artifact_id} rev ${item.revision}` })])]);
    const renderBlocked = (item) => el("li", { class: "mem" }, [el("p", { class: "mem-text", text: item.text }), el("div", { class: "mem-foot" }, [citationButton(item.citation), el("span", { class: "hint", text: item.rule })])]);
    const renderDone = (item) =>
      el("li", { class: "mem" }, [
        el("p", { class: "mem-text", text: item.text }),
        el("div", { class: "mem-foot" }, [citationButton(item.evidence || item.citation), el("span", { class: "hint", text: item.rule })]),
      ]);
    const renderProgress = (item) => el("li", { class: "mem" }, [el("p", { class: "mem-text", text: item.text }), el("div", { class: "mem-foot" }, [citationButton(item.citation), el("span", { class: "hint", text: item.rule })])]);

    const next = el("div", { class: "card" }, [
      el("h2", { text: "下一步" }),
      payload.next_steps.length
        ? el(
            "ol",
            { class: "steps" },
            payload.next_steps.map((step) =>
              el("li", {}, [
                el("div", { text: step.text }),
                el("div", { class: "mem-foot" }, [citationButton(step.citation), el("span", { class: "hint", text: step.reason })]),
              ])
            )
          )
        : el("p", { class: "hint", text: "没有未完成待办，因此不给下一步建议。" }),
      el("p", { class: "rules", text: payload.explanation.join(" ") }),
    ]);

    return [
      controls,
      el("div", { class: "grid" }, [
        section("已完成", payload.completed, renderDone),
        section("当前阻塞", payload.blocked, renderBlocked),
        next,
        section("进度记录原文", payload.recorded_progress, renderProgress),
        el("div", { class: "card wide" }, [
          el("h2", { text: `参考记忆（${payload.references.length} 条）` }),
          el("ul", { class: "plain" }, payload.references.map((item) => renderRef(item))),
        ]),
      ]),
    ];
  },

  search() {
    const box = el("div", { class: "card" }, [
      el("h3", { text: "检索 PowerContext" }),
      el("form", {
        class: "row",
        onsubmit: (event) => {
          event.preventDefault();
          runSearch(event.target.query.value);
        },
      }, [
        el("input", { name: "query", placeholder: "例如：幻读", value: state.searchQuery }),
        el("button", { class: "accent", type: "submit", text: "检索" }),
      ]),
      el("p", {
        class: "hint",
        text: "调用 PowerContext atomic-memory 全文检索（SQLite FTS5 + BM25）。未配置 embedding 模型，所以不宣称语义检索；中文按包内词法投影分词，实测可用。",
      }),
    ]);

    const result = state.searchResult;
    if (!result) return [box];
    if (!result.hits.length) {
      return [box, el("div", { class: "grid" }, [emptyState("没有匹配记忆", result.empty_message || "PowerContext 返回 0 条。")])];
    }
    const rows = result.hits.map((hit) =>
      el("li", { class: "mem" }, [
        el("div", { class: "mem-head" }, [
          el("span", { class: "chip kind", "data-kind": hit.record.kind, text: KIND_LABELS[hit.record.kind] || hit.record.kind }),
          el("span", { class: "chip", text: `rev ${hit.record.revision}` }),
          el("span", { class: "chip", text: `matched_by=${hit.matched_by.join(",")}` }),
          hit.score !== null ? el("span", { class: "chip", text: `score ${hit.score.toFixed(4)}` }) : null,
        ]),
        el("p", { class: "mem-text", text: hit.record.text }),
        el("div", { class: "mem-foot" }, [
          citationButton(hit.record.citation),
          el("span", { class: "mono", text: `${hit.record.artifact_id}` }),
          el("span", { class: "hint", text: `mode=${result.mode}` }),
        ]),
      ])
    );
    return [box, el("div", { class: "grid" }, [el("div", { class: "card wide" }, [el("h3", { text: `命中 ${result.hits.length} 条` }), el("ul", { class: "plain" }, rows)])])];
  },

  tasks() {
    const items = state.tasks || [];
    const creator = el("div", { class: "card" }, [
      el("h3", { text: "新建待办" }),
      el("form", {
        class: "row",
        onsubmit: (event) => {
          event.preventDefault();
          createTask(event.target.text.value);
          event.target.reset();
        },
      }, [el("input", { name: "text", placeholder: "例如：为幻读写一个两事务并发的示例", required: "required" }), el("button", { class: "accent", type: "submit", text: "创建" })]),
    ]);

    if (!items.length) {
      return [creator, el("div", { class: "grid" }, [emptyState("没有待办", "创建一个待办，或导入演示数据。", el("button", { class: "accent", text: "导入演示数据", onclick: () => importDemo() }))])];
    }

    const list = items.map((task) => {
      const seq = task.event_payload ? task.event_payload.seq : 0;
      const action = el("button", {
        class: task.status === "done" ? "ghost" : "accent",
        text: task.status === "done" ? "恢复为待办" : "标记完成",
        onclick: () => updateTaskStatus(task, task.status === "done" ? "todo" : "done", seq),
      });
      return memoryCard(task.memory, [
        el("span", { class: `chip state-${task.status === "done" ? "active" : "retired"}`, text: STATE_LABELS[task.status] }),
        el("span", { class: "chip", text: `事件 seq ${seq}` }),
        action,
        task.status_event ? citationButton(task.status_event.citation) : null,
      ]);
    });

    return [
      creator,
      el("div", { class: "grid" }, [
        el("div", { class: "card wide" }, [el("h3", { text: `待办 · ${items.length} 项` }), el("ul", { class: "plain" }, list)]),
        el("div", { class: "card wide" }, [
          el("h3", { text: "状态模型说明" }),
          el("p", {
            class: "rules",
            text: "完成/恢复会在 PowerContext 追加一条 kind=task_status 的事件记忆（studyflow.task-status.v1），列表按事件 seq 取最新。任务正文 artifact 的 revision 保持 1，界面不声称已修订正文；写入前比较 expected_seq，不一致返回 409 而不是覆盖。",
          }),
        ]),
      ]),
    ];
  },

  export() {
    const payload = state.continuation;
    return [
      el("div", { class: "grid" }, [
        el("div", { class: "card wide" }, [
          el("h2", { text: "复盘初稿" }),
          el("p", { text: "根据当前工作区的记忆与待办状态事件生成 Markdown 初稿，含精确引用和“需本人核对”清单。" }),
          el("p", { class: "hint", text: "生成方式为规则整理（rule-based-v1），不调用生成式模型；没有记录的使用时长、论文数量、提效比例不会被写出。" }),
          el("div", { class: "row" }, [
            el("button", { class: "accent", text: "下载 Markdown（UTF-8）", onclick: () => window.location.assign("/api/review/export") }),
            el("button", { class: "ghost", text: "刷新预览", onclick: () => loadAll() }),
          ]),
        ]),
        el("div", { class: "card wide" }, [
          el("h3", { text: "初稿预览" }),
          el("pre", { class: "mono", style: "white-space:pre-wrap; margin:0", text: state.reviewText || "（尚未生成，请先导入或创建记忆）" }),
        ]),
      ]),
      payload && payload.references.length
        ? el("div", { class: "card" }, [
            el("h3", { text: `本初稿引用了 ${payload.references.length} 条记忆` }),
            el("ul", { class: "plain" }, payload.references.map((item) => el("li", { class: "mem" }, [el("p", { class: "mem-text", text: item.quote }), el("div", { class: "mem-foot" }, [citationButton(item), el("span", { class: "mono", text: `${item.artifact_id} rev ${item.revision}` })])]))),
          ])
        : null,
    ];
  },

  validation() {
    const rows = (state.log || []).map((item) =>
      el("tr", {}, [
        el("td", { class: "mono", text: item.recorded_at || "" }),
        el("td", { text: item.operation || "" }),
        el("td", { class: "mono", text: item.scope_id ? item.scope_id.slice(0, 12) : "" }),
        el("td", {
          class: "mono",
          text: Object.entries(item)
            .filter(([key]) => !["recorded_at", "operation", "scope_id"].includes(key))
            .map(([key, value]) => `${key}=${typeof value === "object" ? JSON.stringify(value) : value}`)
            .join(" ")
            .slice(0, 260),
        }),
      ])
    );
    return [
      el("div", { class: "grid" }, [
        el("div", { class: "card wide" }, [
          el("h2", { text: "验证记录" }),
          el("p", { class: "hint", text: "本表记录应用对 PowerContext 的真实写入、读回、查询与状态变更；不包含密码与令牌。" }),
          rows.length
            ? el("table", {}, [el("tr", {}, [el("th", { text: "时间" }), el("th", { text: "操作" }), el("th", { text: "scope" }), el("th", { text: "关键参数与结果" })]), ...rows])
            : el("p", { class: "hint", text: "还没有记录。" }),
          el("div", { class: "row", style: "margin-top:12px" }, [el("button", { class: "ghost", text: "刷新", onclick: () => loadAll() })]),
        ]),
      ]),
    ];
  },
};

/* --------------------------------------------------------------------- 渲染 */

function render() {
  const nodes = views[state.view] ? views[state.view]() : [];
  view.textContent = "";
  nodes.filter(Boolean).forEach((node) => view.appendChild(node));
  document.querySelectorAll("#nav a").forEach((link) => {
    link.classList.toggle("active", link.dataset.view === state.view);
  });
}

/* --------------------------------------------------------------------- 数据加载 */

async function refreshStatus() {
  try {
    const status = await api.get("/api/status");
    state.status = status;
    setConnection(status);
    return status;
  } catch (error) {
    state.status = { connected: false, message: `状态接口不可用：${error.message}` };
    setConnection(state.status);
    return state.status;
  }
}

async function loadWorkspaces() {
  if (!state.status || !state.status.connected) return;
  try {
    const data = await api.get("/api/workspaces");
    workspaceSelect.textContent = "";
    const names = new Set([...(data.known || []), ...(data.scopes || []).map((item) => item.workspace), data.current]);
    names.forEach((name) => workspaceSelect.appendChild(el("option", { value: name, text: name })));
    workspaceSelect.value = data.current;
  } catch (error) {
    toast(`工作区读取失败：${error.message}`, "error");
  }
}

async function loadMemories(states = state.statesParam, kind = state.kindParam) {
  state.statesParam = states;
  state.kindParam = kind;
  if (!state.status || !state.status.connected) {
    state.memories = [];
    return;
  }
  const data = await api.get("/api/memories", { states, kind: kind || undefined, limit: 300 });
  state.memories = data.items;
  state.listMeta = { count: data.count, limit: data.limit, has_more: data.has_more, note: data.list_note };
}

async function loadTasks() {
  if (!state.status || !state.status.connected) {
    state.tasks = [];
    return;
  }
  state.tasks = (await api.get("/api/tasks")).items;
}

async function loadContinuation(minutes) {
  if (!state.status || !state.status.connected) {
    state.continuation = null;
    return;
  }
  const value = minutes === undefined || minutes === null || minutes === "" ? "" : String(minutes);
  const query = value ? `?available_minutes=${encodeURIComponent(value)}` : "";
  state.continuation = await api.get(`/api/continuation${query}`);
}

async function loadReviewPreview() {
  if (!state.status || !state.status.connected) {
    state.reviewText = "";
    return;
  }
  const response = await fetch("/api/review/export");
  state.reviewText = response.ok ? await response.text() : `生成失败：${response.status}`;
}

async function loadLog() {
  if (!state.status || !state.status.connected) {
    state.log = [];
    return;
  }
  state.log = (await api.get("/api/validation-log?limit=60")).items;
}

async function loadAll() {
  loading("从 PowerContext 读取中…");
  await refreshStatus();
  try {
    await Promise.all([loadWorkspaces(), loadMemories(), loadTasks(), loadContinuation(), loadReviewPreview(), loadLog()]);
    showBanner(state.status && state.status.connected ? state.status.search_note : state.status.message, state.status && state.status.connected ? "" : "error");
  } catch (error) {
    showBanner(`读取失败：${error.message}`, "error");
  }
  render();
}

/* --------------------------------------------------------------------- 动作 */

async function importDemo() {
  try {
    const result = await api.post("/api/samples/import");
    await loadAll();
    toast(
      `演示数据：本次新建 ${result.created_now} 条，跳过重复 ${result.skipped_duplicate} 条，工作区共 ${result.total_memories} 条（scope ${result.scope_id}）`,
      "ok"
    );
  } catch (error) {
    toast(`导入失败：${error.message}`, "error");
  }
}

async function createMemory(form) {
  const payload = {
    kind: form.kind.value,
    text: form.text.value,
    description: form.description.value || null,
  };
  try {
    const created = await api.post("/api/memories", payload);
    form.reset();
    await loadAll();
    toast(`已写入 PowerContext：${created.memory.artifact_id} rev ${created.memory.revision}`, "ok");
  } catch (error) {
    toast(`保存失败：${error.message}`, "error");
  }
}

async function createTask(text) {
  try {
    await api.post("/api/tasks", { text });
    await loadAll();
    toast("待办已创建", "ok");
  } catch (error) {
    toast(`创建失败：${error.message}`, "error");
  }
}

async function forgetMemory(record) {
  const sure = window.confirm(`遗忘这条 ${record.kind} 记忆？\n${record.text.slice(0, 60)}\n\n这是 PowerContext 的 compare-and-set 写入，不是删除文件。`);
  if (!sure) return;
  try {
    const result = await api.del(`/api/memories/${record.artifact_id}`, {
      expected_revision: record.revision,
      expected_state_version: record.state_version,
    });
    await loadAll();
    toast(`已遗忘：${result.memory.artifact_id} 状态 ${result.memory.state}`, "ok");
  } catch (error) {
    if (error.status === 409) {
      await loadAll();
      toast(`版本冲突：${(error.detail && error.detail.message) || error.message}`, "error");
      return;
    }
    toast(`遗忘失败：${error.message}`, "error");
  }
}

async function updateTaskStatus(task, status, expectedSeq) {
  try {
    const result = await api.post(`/api/tasks/${task.memory.artifact_id}/status`, { status, expected_seq: expectedSeq });
    await loadAll();
    toast(`状态已追加为事件 seq ${result.seq}`, "ok");
  } catch (error) {
    if (error.status === 409) {
      await loadAll();
      toast(`版本冲突：${error.detail && error.detail.message ? error.detail.message : error.message}`, "error");
      return;
    }
    toast(`更新失败：${error.message}`, "error");
  }
}

async function runSearch(query) {
  state.searchQuery = query;
  try {
    state.searchResult = await api.post("/api/search", { query, limit: 10 });
    render();
    toast(state.searchResult.hits.length ? `命中 ${state.searchResult.hits.length} 条` : "无结果：应用不会编造答案", state.searchResult.hits.length ? "ok" : "");
  } catch (error) {
    state.searchResult = null;
    toast(`检索失败：${error.message}`, "error");
    render();
  }
}

/* --------------------------------------------------------------------- 连接实时状态 */

async function pollStatus() {
  const previous = state.status && state.status.connected;
  const status = await refreshStatus();
  if (previous && !status.connected) toast("PowerContext 已断开，界面停止显示任何读写结果", "error");
  if (!previous && status.connected) {
    toast("PowerContext 已连接", "ok");
    await loadAll();
  }
}

workspaceSelect.addEventListener("change", async () => {
  const name = workspaceSelect.value;
  loading("切换工作区…");
  try {
    await api.post("/api/workspaces", { workspace: name });
    await loadAll();
    toast(`已切换到工作区「${name}」`, "ok");
  } catch (error) {
    toast(`切换失败：${error.message}`, "error");
    workspaceSelect.value = state.status ? state.status.workspace : name;
    render();
  }
});

document.getElementById("new-workspace").addEventListener("click", async () => {
  const name = window.prompt("新工作区名称");
  if (!name || !name.trim()) return;
  try {
    await api.post("/api/workspaces", { workspace: name.trim() });
    await loadAll();
    toast(`已创建并切换到工作区「${name.trim()}」`, "ok");
  } catch (error) {
    toast(`创建失败：${error.message}`, "error");
  }
});

document.getElementById("import-demo").addEventListener("click", importDemo);

window.addEventListener("hashchange", () => {
  const next = (window.location.hash || "#/overview").replace("#/", "");
  state.view = next === "continue" ? "continue" : next || "overview";
  if (!views[state.view]) state.view = "overview";
  render();
});

(async function bootstrap() {
  const target = (window.location.hash || "").replace("#/", "");
  if (views[target]) state.view = target;
  loading("正在连接 PowerContext…");
  await loadAll();
  setInterval(pollStatus, 8000);
})();
