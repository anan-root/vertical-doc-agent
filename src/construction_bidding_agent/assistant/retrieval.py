"""Lightweight retrieval and reranking for assistant answers."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import math
import re
from typing import Any

from .knowledge_base import builtin_knowledge_items


INTENT_TYPE_WEIGHTS = {
    "project_overview": {"project_summary": 2.0, "project_overview": 2.4},
    "progress": {"project_summary": 2.0, "generation_status": 1.4},
    "next_action": {"project_summary": 1.8, "page_advice": 1.5, "assistant_knowledge": 1.2},
    "score_points": {"score_points": 2.5, "assistant_knowledge": 1.5, "rag_evidence": 1.2},
    "outline": {"outline_preview": 2.2, "assistant_knowledge": 1.4},
    "generation": {"generation_status": 2.0, "assistant_knowledge": 1.6, "rag_evidence": 1.4},
    "generation_summary": {"generation_status": 2.0, "project_summary": 1.3},
    "review_report": {"score_points": 1.8, "generation_status": 1.8, "assistant_knowledge": 1.5, "rag_evidence": 1.2},
    "word": {"assistant_knowledge": 1.8, "project_summary": 1.2},
    "template": {"template_candidate": 2.0, "assistant_knowledge": 1.3},
    "template_boundary": {"assistant_knowledge": 2.2, "template_candidate": 1.5},
    "materials": {"rag_evidence": 2.2, "assistant_knowledge": 1.4},
    "material_ingestion": {"assistant_knowledge": 1.8, "rag_evidence": 1.2},
    "model_ops": {"assistant_knowledge": 1.5, "generation_status": 1.2},
    "risk": {"assistant_knowledge": 2.0, "rag_evidence": 1.6, "score_points": 1.3},
    "queue_preflight": {"assistant_knowledge": 1.9, "generation_status": 1.6, "rag_evidence": 1.2},
    "context_help": {"page_advice": 2.2, "assistant_knowledge": 1.3},
}


def build_lightweight_retrieval_context(
    *,
    message: str,
    intent: str,
    base_context: Sequence[Mapping[str, Any]],
    rag_preview: Mapping[str, Any] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    documents = _context_documents(base_context)
    documents.extend(_knowledge_documents(intent))
    documents.extend(_rag_documents(rag_preview))
    if not documents:
        return []

    query_tokens = _tokenize_query(message)
    if not query_tokens:
        query_tokens = _tokenize_query(intent)
    bm25_scores = _bm25_scores(query_tokens, documents)
    ranked: list[dict[str, Any]] = []
    for index, doc in enumerate(documents):
        bm25 = bm25_scores[index]
        rerank = _rerank_bonus(doc, intent, query_tokens)
        score = bm25 + rerank
        if score <= 0 and doc.get("type") not in {"project_summary", "page_advice"}:
            continue
        ranked.append(
            {
                "type": doc.get("type"),
                "title": doc.get("title"),
                "content": doc.get("content"),
                "source": doc.get("source") or doc.get("type"),
                "category": doc.get("category"),
                "risk_level": doc.get("risk_level"),
                "bm25_score": round(bm25, 4),
                "rerank_score": round(rerank, 4),
                "score": round(score, 4),
                "matched_tokens": sorted(set(query_tokens) & set(doc.get("tokens") or []))[:8],
            }
        )
    ranked.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    return ranked[: max(limit, 0)]


def _context_documents(base_context: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    documents = []
    for item in base_context:
        data = dict(item)
        content = str(data.get("content") or "")
        title = str(data.get("title") or "")
        if not title and not content:
            continue
        data["content"] = content
        data["title"] = title or "上下文"
        data["tokens"] = _tokenize_query(f"{title} {content}")
        documents.append(data)
    return documents


def _knowledge_documents(intent: str) -> list[dict[str, Any]]:
    documents = []
    for item in builtin_knowledge_items():
        title = str(item.get("title") or "")
        content = str(item.get("content") or "")
        tags = [str(tag) for tag in item.get("tags") or []]
        intents = [str(value) for value in item.get("intents") or []]
        documents.append(
            {
                "type": "assistant_knowledge",
                "title": title,
                "content": content,
                "category": item.get("category"),
                "source": item.get("source"),
                "risk_level": item.get("risk_level"),
                "tags": tags,
                "intents": intents,
                "tokens": _tokenize_query(" ".join([title, content, *tags, *intents, intent])),
            }
        )
    return documents


def _rag_documents(rag_preview: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    documents = []
    if not isinstance(rag_preview, Mapping):
        return documents
    for raw in rag_preview.get("results") or []:
        if not isinstance(raw, Mapping):
            continue
        title = str(raw.get("title") or raw.get("section_title") or "智库依据")
        content = str(raw.get("summary") or raw.get("reason") or raw.get("text_preview") or "")
        source = str(raw.get("source_title") or raw.get("source_type_label") or "投标智库")
        knowledge_type = str(raw.get("knowledge_type_label") or raw.get("knowledge_type") or "智库资料")
        documents.append(
            {
                "type": "rag_evidence",
                "title": title,
                "content": content,
                "source": source,
                "category": knowledge_type,
                "risk_level": "medium" if raw.get("knowledge_type") in {"law_regulation", "technical_standard", "enterprise_policy", "review_rule"} else "low",
                "tokens": _tokenize_query(f"{title} {content} {source} {knowledge_type}"),
            }
        )
    return documents


def _bm25_scores(query_tokens: list[str], documents: Sequence[Mapping[str, Any]]) -> list[float]:
    tokenized_docs = [list(doc.get("tokens") or []) for doc in documents]
    doc_count = len(tokenized_docs)
    if not doc_count:
        return []
    avgdl = sum(len(tokens) for tokens in tokenized_docs) / doc_count or 1.0
    df = Counter()
    for tokens in tokenized_docs:
        for token in set(tokens):
            df[token] += 1
    k1 = 1.5
    b = 0.75
    scores = []
    for tokens in tokenized_docs:
        counts = Counter(tokens)
        doc_len = len(tokens) or 1
        score = 0.0
        for token in query_tokens:
            freq = counts.get(token, 0)
            if not freq:
                continue
            idf = math.log(1 + (doc_count - df[token] + 0.5) / (df[token] + 0.5))
            denom = freq + k1 * (1 - b + b * doc_len / avgdl)
            score += idf * (freq * (k1 + 1) / denom)
        scores.append(score)
    return scores


def _rerank_bonus(doc: Mapping[str, Any], intent: str, query_tokens: Sequence[str]) -> float:
    doc_type = str(doc.get("type") or "")
    bonus = INTENT_TYPE_WEIGHTS.get(intent, {}).get(doc_type, 0.0)
    intents = {str(value) for value in doc.get("intents") or []}
    if intent in intents:
        bonus += 1.2
    tags = {str(value) for value in doc.get("tags") or []}
    if tags & set(query_tokens):
        bonus += 0.5
    title_tokens = set(_tokenize_query(doc.get("title")))
    if title_tokens & set(query_tokens):
        bonus += 0.7
    risk_level = str(doc.get("risk_level") or "")
    if intent in {"risk", "review_report", "word"} and risk_level == "high":
        bonus += 0.6
    if doc_type == "rag_evidence" and intent in {"materials", "risk", "generation", "review_report"}:
        bonus += 0.4
    return bonus


def _tokenize_query(value: Any) -> list[str]:
    text = str(value or "").lower()
    latin = re.findall(r"[a-z0-9_]+", text)
    chinese_parts = re.findall(r"[\u4e00-\u9fff]+", text)
    tokens: list[str] = []
    tokens.extend(latin)
    for part in chinese_parts:
        if len(part) <= 2:
            tokens.append(part)
            continue
        for size in (2, 3, 4):
            tokens.extend(part[index : index + size] for index in range(0, max(len(part) - size + 1, 0)))
    stopwords = {"这个", "当前", "一下", "什么", "怎么", "哪些", "有没有", "项目"}
    return [token for token in tokens if token and token not in stopwords]
