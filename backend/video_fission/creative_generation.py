"""Gemini-first creative intent and script generation."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .settings_store import SettingsStore
from .understand.gateway import GeminiGatewayClient
from .understand.normalize import extract_json
from .understand.observe import OBSERVE_CARD_FIELDS

logger = logging.getLogger(__name__)

CANDIDATE_IDS = ("script_a", "script_b", "script_c")


class CreativeGenerationError(RuntimeError):
    pass


def _text(value: Any, limit: int = 1200) -> str:
    return " ".join(str(value or "").split())[:limit]


def _quote_text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(value: Any, limit: int = 8, item_limit: int = 240) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[，,、;；\n]", value)
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item, item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _observe_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    cards: list[dict[str, str]] = []
    for card in value.get("shot_observation_card") or []:
        if not isinstance(card, dict):
            continue
        compact: dict[str, str] = {}
        for key in OBSERVE_CARD_FIELDS:
            text = _text(card.get(key), 800)
            if text:
                compact[key] = text
        if compact:
            cards.append(compact)

    claims: list[dict[str, Any]] = []
    for claim in value.get("raw_claims") or []:
        if not isinstance(claim, dict):
            continue
        text = _quote_text(claim.get("raw_claim"))
        if not text:
            continue
        claims.append(
            {
                "raw_claim_id": _text(claim.get("raw_claim_id"), 80),
                "raw_claim": text,
                "source_type": _text(claim.get("source_type"), 80),
                "scope": _text(claim.get("scope"), 80),
                "language": _text(claim.get("language"), 32),
                "start_time": claim.get("start_time"),
                "end_time": claim.get("end_time"),
            }
        )
        if len(claims) >= 8:
            break

    speech: dict[str, Any] = {}
    evidence = value.get("speech_evidence")
    if isinstance(evidence, dict):
        segments = []
        for item in evidence.get("segments") or []:
            if not isinstance(item, dict):
                continue
            text = _quote_text(item.get("text"))
            if not text:
                continue
            segments.append(
                {
                    "start": item.get("start"),
                    "end": item.get("end"),
                    "text": text,
                }
            )
            if len(segments) >= 16:
                break
        speech = {
            key: value
            for key, value in (
                ("status", _text(evidence.get("status"), 32)),
                ("source", _text(evidence.get("source"), 80)),
                ("language", _text(evidence.get("language"), 32)),
                ("notes", _text(evidence.get("notes"), 300)),
            )
            if value
        }
        if segments:
            speech["segments"] = segments

    result: dict[str, Any] = {}
    if cards:
        result["shot_observation_card"] = cards
    if claims:
        result["raw_claims"] = claims
    if speech.get("segments") or speech.get("notes"):
        result["speech_evidence"] = speech
    return result


def _resolve_bundle(data: Any) -> dict[str, Any]:
    if isinstance(data, str):
        parsed = extract_json(data)
        return _resolve_bundle(parsed)
    if not isinstance(data, dict):
        raise CreativeGenerationError("Gemini 没有返回创意 JSON 对象")
    if isinstance(data.get("intent"), dict) or isinstance(data.get("candidates"), list):
        return data
    for key in ("data", "result", "output", "content", "text", "message", "answer"):
        nested = data.get(key)
        if nested is None:
            continue
        try:
            return _resolve_bundle(nested)
        except CreativeGenerationError:
            continue
    raise CreativeGenerationError("Gemini 返回结果缺少 intent 或 candidates")


def _client(settings_store: SettingsStore, user_id: str | None) -> tuple[GeminiGatewayClient, dict[str, Any]]:
    public, secrets = settings_store.raw(user_id)
    settings = public.get("multimodal") if isinstance(public.get("multimodal"), dict) else {}
    provider = str(settings.get("provider") or "")
    base_url = str(settings.get("base_url") or "").strip()
    if provider != "gemini_gateway" or not base_url:
        raise CreativeGenerationError("多模态 Provider 未配置为 gemini_gateway")
    key = str(secrets.get("multimodal") or "").strip()
    token_url = str(settings.get("token_url") or "").strip()
    if not key and not token_url:
        raise CreativeGenerationError("Gemini 网关未配置 API Key 或换票地址")
    return (
        GeminiGatewayClient(
            base_url=base_url,
            upload_url=str(settings.get("upload_url") or ""),
            token_url=token_url,
            api_key=key,
            model=str(settings.get("model") or ""),
            source=str(settings.get("source") or ""),
            max_tokens=max(int(settings.get("max_tokens") or 12000), 16000),
            timeout_seconds=int(settings.get("timeout_seconds") or 360),
        ),
        settings,
    )


def _prompt(production: dict[str, Any], refs: list[dict[str, Any]]) -> str:
    decomposition = production.get("decomposition") if isinstance(production.get("decomposition"), dict) else {}
    landing = production.get("landing_page") if isinstance(production.get("landing_page"), dict) else {}
    segments = []
    for segment in (decomposition.get("segments") or [])[:24]:
        segments.append(
            {
                "id": segment.get("id"),
                "role": segment.get("role"),
                "start_ms": segment.get("start_ms"),
                "end_ms": segment.get("end_ms"),
                "summary": _text(segment.get("summary"), 500),
                "visual": _text(segment.get("visual"), 500),
                "observe": _observe_context(segment.get("observe")),
            }
        )
    reference_paths = [
        {"path": item.get("path"), "why": _text(item.get("why"), 300)}
        for item in refs[:6]
        if isinstance(item, dict)
    ]
    context = {
        "brief": production.get("brief") or "",
        "source_video": production.get("source_video") or {},
        "opening": decomposition.get("opening") or {},
        "video_summary": decomposition.get("summary") or decomposition.get("description") or "",
        "segments": segments,
        "landing_page": {
            "product": landing.get("product"),
            "audience": landing.get("audience"),
            "selling_points": landing.get("selling_points") or [],
            "benefits": landing.get("benefits") or [],
            "promises": landing.get("promises") or [],
            "parse_status": landing.get("parse_status"),
        },
        "retrieved_references": reference_paths,
        "reference_images": [
            {"name": item.get("name"), "content_type": item.get("content_type")}
            for item in production.get("reference_images") or []
            if isinstance(item, dict)
        ],
    }
    return f"""你是短视频创意导演和分镜编剧。请基于给定参考视频拆解、用户说明、落地页信息和参考图，输出严格 JSON。

任务要求：
1. 先提炼用户真实意图，不要只复述原句；明确目标、受众、核心承诺、真实意图、产品卖点、用户痛点、证据与必要假设。
2. 卖点和痛点必须来自用户说明、落地页、参考视频证据或参考图可见事实。
   缺少证据时明确放入 assumptions，不得虚构功效、认证、测试数据或评价。
3. 生成恰好 3 个差异化脚本候选，id 必须是 script_a、script_b、script_c。
4. 每个候选的 script.shots 必须与原视频时间轴镜头一一对应，顺序、数量、时长、action
   和 source_segment_id 由后端锁定。你只需为每个镜头提供 visual、voiceover、on_screen_text、prompt。
5. 三个方案分别侧重痛点切入、效果利益、信任促单，但都要符合商品事实。
6. 如果收到参考图，用它锁定商品外观、颜色、结构、人物或场景一致性；提示词中写明这些约束。
7. 输出中不要出现“已还原参考”“已按参考对齐”等声称，因为你只负责创意和脚本，尚未生成视频。

只返回以下 JSON 结构，不要 Markdown：
{{
  "intent": {{
    "goal": "用户看完应理解、相信或做什么",
    "true_intent": "用户真正要完成的视频结果",
    "audience": "具体受众和使用情境",
    "selling_points": ["卖点 1", "卖点 2"],
    "pain_points": ["痛点 1", "痛点 2"],
    "promise": "可感知的核心承诺",
    "evidence": ["可展示的证据"],
    "assumptions": ["必要假设"]
  }},
  "skeleton": {{
    "one_liner": "一行骨架",
    "hook": "开场",
    "value": "中段价值",
    "cta": "结尾行动"
  }},
  "candidates": [
    {{
      "id": "script_a",
      "title": "痛点切入",
      "angle": "方案角度",
      "script": {{
        "shots": [
          {{
            "visual": "具体画面",
            "voiceover": "口播原文",
            "on_screen_text": "屏幕字幕",
            "prompt": "给 Seedance 的中文单镜头提示词"
          }}
        ]
      }}
    }}
  ]
}}

创作上下文：
{json.dumps(context, ensure_ascii=False, separators=(",", ":"))}
"""


def generate_creative_bundle(
    settings_store: SettingsStore,
    production: dict[str, Any],
    user_id: str | None = None,
) -> dict[str, Any]:
    client, settings = _client(settings_store, user_id)
    intent = production.get("intent") if isinstance(production.get("intent"), dict) else {}
    refs = list(intent.get("references") or [])
    images = [
        Path(str(item.get("path") or ""))
        for item in production.get("reference_images") or []
        if isinstance(item, dict) and item.get("path")
    ]
    images = [path for path in images if path.is_file()][:3]
    logger.info(
        "creative.gemini_started production=%s model=%s segments=%s images=%s",
        production.get("id"),
        settings.get("model") or "-",
        len((production.get("decomposition") or {}).get("segments") or []),
        len(images),
    )
    response = client.call(
        _prompt(production, refs),
        frames=images or None,
        temperature=0.45,
        max_tokens=max(int(settings.get("max_tokens") or 12000), 16000),
    )
    payload = client.extract_payload(response)
    bundle = _resolve_bundle(payload if payload is not None else response)
    raw_intent = bundle.get("intent") if isinstance(bundle.get("intent"), dict) else {}
    raw_skeleton = bundle.get("skeleton") if isinstance(bundle.get("skeleton"), dict) else {}
    raw_candidates = bundle.get("candidates") if isinstance(bundle.get("candidates"), list) else []
    if len(raw_candidates) != 3:
        raise CreativeGenerationError("Gemini 没有返回恰好 3 个脚本候选")

    normalized_intent = {
        **raw_intent,
        "goal": _text(raw_intent.get("goal"), 600),
        "true_intent": _text(raw_intent.get("true_intent"), 800),
        "audience": _text(raw_intent.get("audience"), 600),
        "selling_points": _text_list(raw_intent.get("selling_points")),
        "pain_points": _text_list(raw_intent.get("pain_points")),
        "promise": _text(raw_intent.get("promise"), 600),
        "evidence": _text_list(raw_intent.get("evidence")),
        "assumptions": _text_list(raw_intent.get("assumptions")),
        "references": refs,
        "generation_provider": "gemini_gateway",
        "generation_model": str(settings.get("model") or ""),
        "generated_at": datetime.now(UTC).isoformat(),
    }
    normalized_skeleton = {
        **raw_skeleton,
        "one_liner": _text(raw_skeleton.get("one_liner"), 600),
        "hook": _text(raw_skeleton.get("hook"), 600),
        "value": _text(raw_skeleton.get("value"), 600),
        "cta": _text(raw_skeleton.get("cta"), 600),
    }
    candidates: list[dict[str, Any]] = []
    for index, item in enumerate(raw_candidates):
        if not isinstance(item, dict):
            raise CreativeGenerationError("Gemini 返回了无效脚本候选")
        candidate_id = CANDIDATE_IDS[index]
        script = item.get("script") if isinstance(item.get("script"), dict) else item
        candidates.append(
            {
                "id": candidate_id,
                "title": _text(item.get("title"), 120) or {
                    "script_a": "痛点切入",
                    "script_b": "效果利益",
                    "script_c": "信任促单",
                }[candidate_id],
                "angle": _text(item.get("angle"), 500),
                "script": script,
            }
        )
    logger.info(
        "creative.gemini_completed production=%s candidates=%s model=%s",
        production.get("id"),
        len(candidates),
        settings.get("model") or "-",
    )
    return {
        "intent": normalized_intent,
        "skeleton": normalized_skeleton,
        "script_candidates": candidates,
        "provider": "gemini_gateway",
        "model": str(settings.get("model") or ""),
    }
