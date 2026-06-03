"""生成技术标目录人工复核静态页面。"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def render_outline_review_page(views: list[dict[str, Any]]) -> str:
    data = json.dumps(views, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>技术标目录人工复核</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
      color: #1f2933;
      background: #f5f7fa;
    }}
    .app {{
      display: grid;
      grid-template-columns: 320px 1fr;
      min-height: 100vh;
    }}
    aside {{
      border-right: 1px solid #d9e2ec;
      background: #ffffff;
      padding: 18px 16px;
      overflow: auto;
    }}
    main {{
      padding: 18px 22px 32px;
      overflow: auto;
    }}
    h1 {{
      margin: 0 0 14px;
      font-size: 20px;
      line-height: 1.3;
    }}
    h2 {{
      margin: 20px 0 10px;
      font-size: 16px;
    }}
    select, input {{
      width: 100%;
      height: 36px;
      border: 1px solid #bcccdc;
      border-radius: 6px;
      padding: 0 10px;
      background: #ffffff;
      font-size: 14px;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(120px, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }}
    .stat {{
      background: #ffffff;
      border: 1px solid #d9e2ec;
      border-radius: 8px;
      padding: 12px;
    }}
    .stat span {{
      display: block;
      color: #627d98;
      font-size: 12px;
      margin-bottom: 6px;
    }}
    .stat strong {{
      font-size: 22px;
    }}
    .tabs {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin: 12px 0 14px;
    }}
    button {{
      height: 34px;
      border: 1px solid #bcccdc;
      border-radius: 6px;
      background: #ffffff;
      padding: 0 12px;
      cursor: pointer;
    }}
    button.active {{
      border-color: #1d4ed8;
      color: #1d4ed8;
      background: #eff6ff;
    }}
    .tree {{
      background: #ffffff;
      border: 1px solid #d9e2ec;
      border-radius: 8px;
      padding: 12px 0;
    }}
    .node {{
      display: grid;
      grid-template-columns: 88px 1fr auto;
      gap: 10px;
      min-height: 32px;
      align-items: center;
      padding: 4px 14px;
      border-left: 3px solid transparent;
    }}
    .node.level-1 {{
      margin-top: 8px;
      font-weight: 700;
      background: #f8fafc;
      border-left-color: #1d4ed8;
    }}
    .node.level-2 {{ padding-left: 34px; }}
    .node.level-3 {{ padding-left: 62px; color: #334e68; }}
    .number {{ color: #52606d; font-variant-numeric: tabular-nums; }}
    .title {{ line-height: 1.45; }}
    .badges {{
      display: flex;
      gap: 6px;
      align-items: center;
      justify-content: flex-end;
      flex-wrap: wrap;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      border-radius: 999px;
      padding: 0 8px;
      font-size: 12px;
      background: #eef2f7;
      color: #334e68;
    }}
    .badge.locked {{ background: #e0f2fe; color: #075985; }}
    .badge.review {{ background: #fef3c7; color: #92400e; }}
    .badge.ready {{ background: #dcfce7; color: #166534; }}
    .queue {{
      display: grid;
      gap: 8px;
      margin-top: 8px;
    }}
    .queue-item {{
      border: 1px solid #f0c36d;
      border-radius: 8px;
      background: #fffbeb;
      padding: 10px;
      font-size: 13px;
      line-height: 1.5;
    }}
    .empty {{
      color: #627d98;
      background: #ffffff;
      border: 1px dashed #bcccdc;
      border-radius: 8px;
      padding: 14px;
    }}
    @media (max-width: 900px) {{
      .app {{ grid-template-columns: 1fr; }}
      aside {{ border-right: 0; border-bottom: 1px solid #d9e2ec; }}
      .stats {{ grid-template-columns: repeat(2, 1fr); }}
      .node {{ grid-template-columns: 74px 1fr; }}
      .badges {{ grid-column: 2; justify-content: flex-start; }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <h1>技术标目录人工复核</h1>
      <label>项目</label>
      <select id="projectSelect"></select>
      <h2>筛选</h2>
      <input id="searchInput" placeholder="搜索目录标题" />
      <h2>待复核</h2>
      <div id="reviewQueue" class="queue"></div>
    </aside>
    <main>
      <div class="stats" id="stats"></div>
      <div class="tabs" id="tabs"></div>
      <section class="tree" id="tree"></section>
    </main>
  </div>
  <script>
    const views = {data};
    let currentProject = 0;
    let currentDomain = "all";

    const projectSelect = document.getElementById("projectSelect");
    const searchInput = document.getElementById("searchInput");
    const statsEl = document.getElementById("stats");
    const tabsEl = document.getElementById("tabs");
    const treeEl = document.getElementById("tree");
    const reviewQueueEl = document.getElementById("reviewQueue");

    function labelForView(view, index) {{
      const id = view.outline_id || `项目${{index + 1}}`;
      return id.replace(/^batch_tender_/, "第").slice(0, 46);
    }}

    function init() {{
      projectSelect.innerHTML = views.map((view, index) => `<option value="${{index}}">${{escapeHtml(labelForView(view, index))}}</option>`).join("");
      projectSelect.addEventListener("change", () => {{
        currentProject = Number(projectSelect.value);
        currentDomain = "all";
        render();
      }});
      searchInput.addEventListener("input", render);
      render();
    }}

    function render() {{
      const view = views[currentProject];
      renderStats(view);
      renderTabs(view);
      renderQueue(view);
      renderTree(view);
    }}

    function renderStats(view) {{
      const s = view.summary || {{}};
      const cards = [
        ["一级目录", s.level_1_count || 0],
        ["目录节点", s.node_count || 0],
        ["待复核", s.pending_review_count || 0],
        ["补强应用", `${{s.refinement?.applied_count || 0}}/${{s.refinement?.task_count || 0}}`],
      ];
      statsEl.innerHTML = cards.map(([label, value]) => `<div class="stat"><span>${{label}}</span><strong>${{value}}</strong></div>`).join("");
    }}

    function renderTabs(view) {{
      const tabs = [{{ domain: "all", label: "全部", level_1_count: view.summary?.level_1_count || 0 }}, ...(view.domain_tabs || [])];
      tabsEl.innerHTML = tabs.map(tab => `<button class="${{tab.domain === currentDomain ? "active" : ""}}" data-domain="${{tab.domain}}">${{escapeHtml(tab.label)}} ${{tab.level_1_count || ""}}</button>`).join("");
      tabsEl.querySelectorAll("button").forEach(button => {{
        button.addEventListener("click", () => {{
          currentDomain = button.dataset.domain;
          render();
        }});
      }});
    }}

    function renderQueue(view) {{
      const queue = view.review_queue || [];
      if (!queue.length) {{
        reviewQueueEl.innerHTML = `<div class="empty">暂无待复核项</div>`;
        return;
      }}
      reviewQueueEl.innerHTML = queue.map(item => `
        <div class="queue-item">
          <strong>${{escapeHtml(item.target_number || "")}} ${{escapeHtml(item.target_title || item.item || "")}}</strong><br>
          ${{escapeHtml(item.reason || "")}}
        </div>
      `).join("");
    }}

    function renderTree(view) {{
      const query = searchInput.value.trim();
      const nodes = (view.tree || []).filter(node => currentDomain === "all" || node.domain === currentDomain);
      const html = nodes.map(node => renderNode(node, query)).filter(Boolean).join("");
      treeEl.innerHTML = html || `<div class="empty">没有匹配的目录</div>`;
    }}

    function renderNode(node, query) {{
      const childHtml = (node.children || []).map(child => renderNode(child, query)).filter(Boolean).join("");
      const matched = !query || (node.title || "").includes(query) || childHtml;
      if (!matched) return "";
      const statusClass = node.review_status === "pending_review" ? "review" : "ready";
      const lockBadge = node.title_locked ? `<span class="badge locked">一级锁定</span>` : "";
      const statusBadge = `<span class="badge ${{statusClass}}">${{node.review_status || ""}}</span>`;
      return `
        <div class="node level-${{node.level}}">
          <div class="number">${{escapeHtml(node.number || "")}}</div>
          <div class="title">${{escapeHtml(node.title || "")}}</div>
          <div class="badges">${{lockBadge}}${{statusBadge}}<span class="badge">${{escapeHtml(node.source_label || node.title_source || "")}}</span></div>
        </div>
        ${{childHtml}}
      `;
    }}

    function escapeHtml(value) {{
      return String(value ?? "").replace(/[&<>"']/g, char => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }}[char]));
    }}

    init();
  </script>
</body>
</html>
"""


def write_outline_review_page(views: list[dict[str, Any]], html_path: str | Path) -> None:
    target = Path(html_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_outline_review_page(views), encoding="utf-8")
