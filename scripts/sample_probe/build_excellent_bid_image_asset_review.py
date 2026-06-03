"""生成优秀标书图片资产人工复核 HTML 页面。"""

from __future__ import annotations

import argparse
import html
import json
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


try:
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
except Exception:  # pragma: no cover - 仅作为无 Pillow 环境的降级保护
    Image = None  # type: ignore[assignment]


@dataclass(slots=True)
class ThumbnailRecord:
    image_url: str = ""
    status: str = "missing"
    message: str = ""


def main() -> int:
    parser = argparse.ArgumentParser(description="生成优秀标书图片资产人工复核 HTML 页面。")
    parser.add_argument(
        "--library-json",
        required=True,
        help="统一优秀标书素材库 JSON 路径。",
    )
    parser.add_argument(
        "--html-out",
        default=str(ROOT / "outputs" / "reports" / "excellent_bid_image_asset_review.html"),
        help="HTML 复核页输出路径。",
    )
    parser.add_argument(
        "--asset-dir",
        default=str(ROOT / "outputs" / "reports" / "assets" / "excellent_bid_image_asset_review"),
        help="图片缩略图输出目录。",
    )
    parser.add_argument(
        "--max-assets",
        type=int,
        default=0,
        help="最多展示图片资产数；0 表示全部展示。",
    )
    parser.add_argument(
        "--thumbnail-size",
        type=int,
        default=420,
        help="缩略图最长边像素。",
    )
    args = parser.parse_args()

    library_path = _resolve_path(args.library_json)
    html_path = _resolve_path(args.html_out)
    asset_dir = _resolve_path(args.asset_dir)

    library = json.loads(library_path.read_text(encoding="utf-8"))
    assets = list(library.get("image_assets") or [])
    if args.max_assets > 0:
        assets = assets[: args.max_assets]

    source_docx_paths = _source_docx_paths(library)
    thumbnails = _build_thumbnails(
        assets,
        source_docx_paths=source_docx_paths,
        asset_dir=asset_dir,
        html_dir=html_path.parent,
        thumbnail_size=args.thumbnail_size,
    )
    html_text = render_image_asset_review_html(
        library,
        assets=assets,
        thumbnails=thumbnails,
        library_path=library_path,
    )

    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_text, encoding="utf-8")

    print(f"Review HTML: {html_path.resolve()}")
    print(f"Thumbnail dir: {asset_dir.resolve()}")
    print(
        "Counts: "
        f"assets={len(assets)}, "
        f"thumbnails={sum(1 for item in thumbnails.values() if item.status == 'ok')}, "
        f"missing={sum(1 for item in thumbnails.values() if item.status != 'ok')}, "
        f"review_required={sum(1 for item in assets if item.get('review_required'))}"
    )
    return 0


def render_image_asset_review_html(
    library: dict[str, Any],
    *,
    assets: list[dict[str, Any]],
    thumbnails: dict[str, ThumbnailRecord],
    library_path: Path,
) -> str:
    source_names = {
        str(source.get("source_id")): str(source.get("source_name") or source.get("source_id"))
        for source in library.get("sources") or []
    }
    sorted_assets = sorted(
        assets,
        key=lambda asset: (
            not bool(asset.get("review_required")),
            str(asset.get("source_id") or ""),
            str(asset.get("material_slice_id") or ""),
            int(asset.get("table_index") or 0),
            int(asset.get("row_index") or 0),
            int(asset.get("cell_index") or 0),
        ),
    )
    summary = _summary(library, sorted_assets, thumbnails)
    cards = "\n".join(
        _render_asset_card(asset, thumbnails.get(_asset_id(asset), ThumbnailRecord()), source_names)
        for asset in sorted_assets
    )
    source_options = "\n".join(
        f'<option value="{_h(source_id)}">{_h(name)}</option>'
        for source_id, name in sorted(source_names.items())
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>优秀标书图片资产复核</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --line: #d9dee7;
      --text: #1f2937;
      --muted: #667085;
      --accent: #2563eb;
      --warn: #b45309;
      --danger: #b42318;
      --ok: #047857;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
      font-size: 14px;
      line-height: 1.55;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 10;
      background: rgba(246, 247, 249, 0.96);
      border-bottom: 1px solid var(--line);
      padding: 18px 24px 14px;
      backdrop-filter: blur(8px);
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: 22px;
      font-weight: 700;
      letter-spacing: 0;
    }}
    .meta, .summary {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 10px;
    }}
    .pill {{
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      padding: 4px 8px;
      color: var(--muted);
      white-space: nowrap;
    }}
    .toolbar {{
      display: grid;
      grid-template-columns: 1.6fr repeat(4, minmax(130px, 1fr));
      gap: 8px;
      max-width: 1320px;
    }}
    input, select {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      padding: 8px 10px;
      font: inherit;
    }}
    main {{
      padding: 18px 24px 32px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
      gap: 14px;
      max-width: 1600px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      display: grid;
      grid-template-rows: auto 1fr;
      min-height: 520px;
    }}
    .preview {{
      height: 260px;
      display: grid;
      place-items: center;
      background: #eef2f7;
      border-bottom: 1px solid var(--line);
    }}
    .preview img {{
      max-width: 100%;
      max-height: 260px;
      object-fit: contain;
      display: block;
    }}
    .no-preview {{
      color: var(--muted);
      padding: 16px;
      text-align: center;
    }}
    .body {{
      padding: 12px 14px 14px;
      display: grid;
      gap: 8px;
      align-content: start;
    }}
    .title-row {{
      display: flex;
      gap: 8px;
      justify-content: space-between;
      align-items: flex-start;
    }}
    .asset-id {{
      font-family: Consolas, monospace;
      font-size: 12px;
      color: var(--muted);
      overflow-wrap: anywhere;
    }}
    .badge {{
      border-radius: 5px;
      padding: 2px 6px;
      font-size: 12px;
      white-space: nowrap;
      border: 1px solid var(--line);
    }}
    .badge.review {{ color: var(--danger); border-color: #f0b4ae; background: #fff4f2; }}
    .badge.ready {{ color: var(--ok); border-color: #9bd4bf; background: #eefbf5; }}
    .badge.risk-high {{ color: var(--danger); }}
    .badge.risk-medium {{ color: var(--warn); }}
    .field strong {{
      display: inline-block;
      min-width: 76px;
      color: #344054;
    }}
    .field {{
      overflow-wrap: anywhere;
    }}
    .caption {{
      font-size: 16px;
      font-weight: 700;
    }}
    .muted {{
      color: var(--muted);
    }}
    .small {{
      font-size: 12px;
    }}
    @media (max-width: 860px) {{
      header, main {{ padding-left: 14px; padding-right: 14px; }}
      .toolbar {{ grid-template-columns: 1fr 1fr; }}
      .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>优秀标书图片资产复核</h1>
    <div class="meta">
      <span class="pill">素材库：{_h(str(library.get("library_id") or "-"))}</span>
      <span class="pill">来源：{_h(str(library_path))}</span>
    </div>
    <div class="summary">{summary}</div>
    <div class="toolbar">
      <input id="q" type="search" placeholder="搜索标题、附近文字、章节、图片 ID">
      <select id="review">
        <option value="">复核状态：全部</option>
        <option value="yes">需人工复核</option>
        <option value="no">可候选复用</option>
      </select>
      <select id="reuse">
        <option value="">复用等级：全部</option>
        <option value="direct_reuse">direct_reuse</option>
        <option value="candidate_reuse">candidate_reuse</option>
        <option value="manual_review">manual_review</option>
      </select>
      <select id="risk">
        <option value="">风险：全部</option>
        <option value="low">low</option>
        <option value="medium">medium</option>
        <option value="high">high</option>
      </select>
      <select id="source">
        <option value="">来源：全部</option>
        {source_options}
      </select>
    </div>
  </header>
  <main>
    <section class="grid" id="cards">
      {cards}
    </section>
  </main>
  <script>
    const controls = ["q", "review", "reuse", "risk", "source"].map(id => document.getElementById(id));
    const cards = Array.from(document.querySelectorAll(".card"));
    function applyFilters() {{
      const q = document.getElementById("q").value.trim().toLowerCase();
      const review = document.getElementById("review").value;
      const reuse = document.getElementById("reuse").value;
      const risk = document.getElementById("risk").value;
      const source = document.getElementById("source").value;
      for (const card of cards) {{
        const ok =
          (!q || card.dataset.text.includes(q)) &&
          (!review || card.dataset.review === review) &&
          (!reuse || card.dataset.reuse === reuse) &&
          (!risk || card.dataset.risk === risk) &&
          (!source || card.dataset.source === source);
        card.style.display = ok ? "" : "none";
      }}
    }}
    controls.forEach(control => control.addEventListener("input", applyFilters));
  </script>
</body>
</html>
"""


def _build_thumbnails(
    assets: list[dict[str, Any]],
    *,
    source_docx_paths: dict[str, Path],
    asset_dir: Path,
    html_dir: Path,
    thumbnail_size: int,
) -> dict[str, ThumbnailRecord]:
    asset_dir.mkdir(parents=True, exist_ok=True)
    records: dict[str, ThumbnailRecord] = {}
    zip_cache: dict[str, zipfile.ZipFile] = {}
    try:
        for asset in assets:
            asset_id = _asset_id(asset)
            source_id = str(asset.get("source_id") or "")
            docx_path = source_docx_paths.get(source_id)
            if not docx_path or not docx_path.exists():
                records[asset_id] = ThumbnailRecord(message="未找到来源 DOCX。")
                continue
            part_name = _normalized_part_name(asset)
            if not part_name:
                records[asset_id] = ThumbnailRecord(message="图片 part_name 为空。")
                continue
            try:
                zip_file = zip_cache.setdefault(source_id, zipfile.ZipFile(docx_path))
                image_bytes = zip_file.read(part_name)
            except Exception as exc:
                records[asset_id] = ThumbnailRecord(message=f"无法从 DOCX 读取图片：{exc}")
                continue

            thumbnail_path = asset_dir / f"{_safe_filename(asset_id)}.jpg"
            record = _write_thumbnail(
                image_bytes,
                thumbnail_path=thumbnail_path,
                html_dir=html_dir,
                thumbnail_size=thumbnail_size,
            )
            records[asset_id] = record
    finally:
        for zip_file in zip_cache.values():
            zip_file.close()
    return records


def _write_thumbnail(
    image_bytes: bytes,
    *,
    thumbnail_path: Path,
    html_dir: Path,
    thumbnail_size: int,
) -> ThumbnailRecord:
    if Image is None:
        return ThumbnailRecord(message="当前环境缺少 Pillow，无法生成缩略图。")
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image.thumbnail((thumbnail_size, thumbnail_size))
            if image.mode in {"RGBA", "LA", "P"}:
                background = Image.new("RGB", image.size, "white")
                alpha = image.getchannel("A") if image.mode in {"RGBA", "LA"} else None
                background.paste(image.convert("RGBA"), mask=alpha)
                image = background
            else:
                image = image.convert("RGB")
            thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(thumbnail_path, "JPEG", quality=86, optimize=True)
    except Exception as exc:
        return ThumbnailRecord(message=f"缩略图生成失败：{exc}")
    return ThumbnailRecord(
        image_url=_relative_url(thumbnail_path, html_dir),
        status="ok",
    )


def _render_asset_card(
    asset: dict[str, Any],
    thumbnail: ThumbnailRecord,
    source_names: dict[str, str],
) -> str:
    asset_id = _asset_id(asset)
    source_id = str(asset.get("source_id") or "")
    caption = str(asset.get("caption_actual") or "")
    candidates = "；".join(str(item) for item in (asset.get("caption_candidates") or [])[:4])
    nearby_text = str(asset.get("nearby_text") or "")
    section_path = " > ".join(str(item) for item in asset.get("section_path") or [])
    column_caption = " / ".join(
        item
        for item in [
            str(asset.get("above_cell_text") or ""),
            str(asset.get("below_cell_text") or ""),
        ]
        if item
    )
    review_required = bool(asset.get("review_required"))
    review_value = "yes" if review_required else "no"
    text_blob = " ".join(
        [
            asset_id,
            caption,
            candidates,
            nearby_text,
            section_path,
            str(asset.get("tags") or ""),
            str(asset.get("part_name") or ""),
            column_caption,
        ]
    ).lower()
    preview = (
        f'<img src="{_h(thumbnail.image_url)}" alt="{_h(caption or asset_id)}">'
        if thumbnail.status == "ok"
        else f'<div class="no-preview">{_h(thumbnail.message or "无图片预览")}</div>'
    )
    review_badge = (
        '<span class="badge review">需复核</span>'
        if review_required
        else '<span class="badge ready">候选复用</span>'
    )
    risk = str(asset.get("project_specific_risk") or "-")
    return f"""
<article class="card"
  data-review="{review_value}"
  data-reuse="{_h(str(asset.get("reuse_level") or ""))}"
  data-risk="{_h(risk)}"
  data-source="{_h(source_id)}"
  data-text="{_h(text_blob)}">
  <div class="preview">{preview}</div>
  <div class="body">
    <div class="title-row">
      <div class="asset-id">{_h(asset_id)}</div>
      <div>{review_badge}</div>
    </div>
    <div class="caption">{_h(caption or "未提取到稳定图片说明")}</div>
    <div class="field"><strong>候选说明</strong>{_h(candidates or "-")}</div>
    <div class="field"><strong>同列文字</strong>{_h(column_caption or "-")}</div>
    <div class="field"><strong>附近文字</strong>{_h(_shorten(nearby_text, 260) or "-")}</div>
    <div class="field"><strong>章节</strong>{_h(section_path or "-")}</div>
    <div class="field"><strong>来源</strong>{_h(source_names.get(source_id, source_id))}</div>
    <div class="field small muted">
      <strong>定位</strong>
      table={_h(str(asset.get("table_index")))} /
      row={_h(str(asset.get("row_index")))} /
      cell={_h(str(asset.get("cell_index")))}
    </div>
    <div class="field small muted"><strong>part</strong>{_h(str(asset.get("part_name") or "-"))}</div>
    <div class="field small">
      <span class="badge">{_h(str(asset.get("reuse_level") or "-"))}</span>
      <span class="badge risk-{_h(risk)}">{_h(risk)}</span>
      <span class="badge">{_h("、".join(str(tag) for tag in asset.get("tags") or []) or "无标签")}</span>
    </div>
    <div class="field small muted"><strong>复核原因</strong>{_h(str(asset.get("review_reason") or "-"))}</div>
  </div>
</article>
"""


def _summary(
    library: dict[str, Any],
    assets: list[dict[str, Any]],
    thumbnails: dict[str, ThumbnailRecord],
) -> str:
    reuse_counts = Counter(str(asset.get("reuse_level") or "-") for asset in assets)
    risk_counts = Counter(str(asset.get("project_specific_risk") or "-") for asset in assets)
    items = [
        f"图片资产：{len(assets)} / 素材库记录：{library.get('image_asset_count', '-')}",
        f"需复核：{sum(1 for asset in assets if asset.get('review_required'))}",
        f"已生成缩略图：{sum(1 for item in thumbnails.values() if item.status == 'ok')}",
        f"缺少预览：{sum(1 for item in thumbnails.values() if item.status != 'ok')}",
        f"复用等级：{_format_counter(reuse_counts)}",
        f"风险：{_format_counter(risk_counts)}",
    ]
    return "".join(f'<span class="pill">{_h(item)}</span>' for item in items)


def _source_docx_paths(library: dict[str, Any]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for source in library.get("sources") or []:
        source_id = str(source.get("source_id") or "")
        for raw_path in source.get("source_paths") or []:
            path = _resolve_path(raw_path)
            if path.suffix.lower() == ".docx":
                result[source_id] = path
                break
    return result


def _normalized_part_name(asset: dict[str, Any]) -> str:
    part_name = str(asset.get("part_name") or "").replace("\\", "/").lstrip("/")
    if part_name:
        return part_name
    target = str(asset.get("target") or "").replace("\\", "/").lstrip("/")
    if not target:
        return ""
    if target.startswith("word/"):
        return target
    if target.startswith("media/"):
        return f"word/{target}"
    return target


def _asset_id(asset: dict[str, Any]) -> str:
    return str(asset.get("image_asset_id") or asset.get("image_id") or "unknown")


def _safe_filename(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)[:180]


def _resolve_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return ROOT / candidate


def _relative_url(path: Path, base_dir: Path) -> str:
    try:
        relative = path.resolve().relative_to(base_dir.resolve())
    except ValueError:
        relative = path.resolve()
    return relative.as_posix()


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _format_counter(counter: Counter[str]) -> str:
    return "，".join(f"{key}={value}" for key, value in sorted(counter.items())) or "-"


def _h(value: str) -> str:
    return html.escape(str(value), quote=True)


if __name__ == "__main__":
    raise SystemExit(main())
