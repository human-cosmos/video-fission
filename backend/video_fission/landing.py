from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any

from .logging_utils import clip_text, safe_url

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
MAX_BYTES = 1_500_000
FETCH_TIMEOUT = 8
BLOCKED_HOSTS = {
    "localhost",
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
}

FetchFn = Callable[[str], tuple[str, str]]


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.headings: list[str] = []
        self.lists: list[str] = []
        self.paragraphs: list[str] = []
        self._capture = ""
        self._tag = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): (value or "") for key, value in attrs}
        self._tag = tag.lower()
        if self._tag == "title":
            self._in_title = True
        elif self._tag == "meta":
            name = (attrs_dict.get("name") or attrs_dict.get("property") or "").lower()
            content = (attrs_dict.get("content") or "").strip()
            if name and content:
                self.meta[name] = content
        elif self._tag in {"h1", "h2", "h3", "li", "p"}:
            self._capture = ""

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        text = re.sub(r"\s+", " ", self._capture).strip()
        if tag == "title":
            self._in_title = False
        elif tag in {"h1", "h2", "h3"} and text:
            self.headings.append(text)
        elif tag == "li" and text:
            self.lists.append(text)
        elif tag == "p" and text:
            self.paragraphs.append(text)
        self._capture = ""
        self._tag = ""

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._tag in {"h1", "h2", "h3", "li", "p"}:
            self._capture += data


def extract_urls(text: str) -> list[str]:
    found: list[str] = []
    for match in URL_RE.findall(text or ""):
        cleaned = match.rstrip(").,，。;；]}")
        if cleaned not in found:
            found.append(cleaned)
    return found


def _blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or ip in ipaddress.ip_network("169.254.0.0/16")
        or ip in ipaddress.ip_network("100.64.0.0/10")
        or ip in ipaddress.ip_network("fd00::/8")
        or ip in ipaddress.ip_network("::1/128")
    )


def assert_public_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("只允许 http/https 落地页链接")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host or host in BLOCKED_HOSTS or host.endswith(".localhost"):
        raise ValueError("落地页地址被 SSRF 防护拒绝")
    try:
        if _blocked_ip(ipaddress.ip_address(host)):
            raise ValueError("落地页地址被 SSRF 防护拒绝")
    except ValueError as exc:
        if "SSRF" in str(exc):
            raise
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except OSError as exc:
        raise ValueError(f"无法解析落地页主机：{host}") from exc
    for info in infos:
        raw = info[4][0]
        try:
            if _blocked_ip(ipaddress.ip_address(raw)):
                raise ValueError("落地页地址被 SSRF 防护拒绝")
        except ValueError:
            raise
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        assert_public_url(newurl)
        return urllib.request.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl
        )


def safe_fetch(url: str, timeout: int = FETCH_TIMEOUT, max_bytes: int = MAX_BYTES) -> tuple[str, str]:
    assert_public_url(url)
    started = time.perf_counter()
    logger.info("landing.http_request_started url=%s timeout_s=%s max_bytes=%s", safe_url(url), timeout, max_bytes)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "video-fission-landing/1.0", "Accept": "text/html,application/xhtml+xml"},
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            final = response.geturl() or url
            assert_public_url(final)
            content_type = str(response.headers.get("Content-Type") or "")
            data = response.read(max_bytes + 1)
    except ValueError:
        logger.warning("landing.http_request_rejected url=%s", safe_url(url))
        raise
    except Exception as exc:
        logger.warning(
            "landing.http_request_failed url=%s error=%s elapsed_ms=%.1f",
            safe_url(url),
            exc.__class__.__name__,
            (time.perf_counter() - started) * 1000,
            exc_info=True,
        )
        raise ValueError(f"落地页抓取失败：{exc.__class__.__name__}") from exc
    if len(data) > max_bytes:
        data = data[:max_bytes]
    charset = "utf-8"
    if "charset=" in content_type.lower():
        charset = content_type.lower().split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
    try:
        text = data.decode(charset, errors="replace")
    except LookupError:
        text = data.decode("utf-8", errors="replace")
    logger.info(
        "landing.http_request_completed url=%s final=%s status=%s content_type=%s bytes=%s charset=%s elapsed_ms=%.1f",
        safe_url(url),
        safe_url(final),
        getattr(response, "status", "-"),
        content_type or "-",
        len(data),
        charset,
        (time.perf_counter() - started) * 1000,
    )
    return text, final


def extract_from_html(html: str, source_url: str = "", method: str = "local_extract") -> dict[str, Any]:
    parser = _HTMLText()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        pass
    title = re.sub(r"\s+", " ", parser.title).strip()
    product = (
        parser.meta.get("og:title")
        or parser.meta.get("twitter:title")
        or title
        or (parser.headings[0] if parser.headings else "")
    )
    description = (
        parser.meta.get("og:description")
        or parser.meta.get("description")
        or parser.meta.get("twitter:description")
        or ""
    )
    audience = parser.meta.get("audience") or ""
    selling_points = list(dict.fromkeys(parser.headings[1:6] + parser.lists[:8]))
    if description and description not in selling_points:
        selling_points.insert(0, description)
    benefits = [item for item in parser.lists[8:14] if item not in selling_points]
    promises = [item for item in parser.paragraphs[:4] if item and item not in selling_points]
    excerpt_parts = [title, description] + parser.headings[:3] + parser.lists[:6]
    raw_excerpt = "\n".join(part for part in excerpt_parts if part)[:1200]
    has_body = bool(product or selling_points or raw_excerpt)
    if not has_body:
        status = "failed"
    elif product and len(selling_points) >= 1:
        status = "ok"
    else:
        status = "partial"
    return {
        "product": product[:200],
        "selling_points": selling_points[:12],
        "benefits": benefits[:8],
        "audience": audience[:200],
        "promises": promises[:8],
        "source_url": source_url,
        "raw_excerpt": raw_excerpt,
        "method": method,
        "parse_status": status,
    }


def extract_from_text(text: str, source_url: str = "", method: str = "pasted_text") -> dict[str, Any]:
    stripped = re.sub(r"https?://\S+", " ", text or "")
    stripped = re.sub(r"<[^>]+>", " ", stripped)
    lines = [re.sub(r"\s+", " ", line).strip(" -•*\t") for line in stripped.splitlines()]
    lines = [line for line in lines if len(line) >= 4]
    product = lines[0][:200] if lines else ""
    selling_points = lines[1:9]
    raw_excerpt = "\n".join(lines[:12])[:1200]
    status = "ok" if len(selling_points) >= 2 else ("partial" if lines else "failed")
    return {
        "product": product,
        "selling_points": selling_points,
        "benefits": [],
        "audience": "",
        "promises": lines[9:13],
        "source_url": source_url,
        "raw_excerpt": raw_excerpt,
        "method": method,
        "parse_status": status,
    }


def failed_landing(source_url: str = "", reason: str = "") -> dict[str, Any]:
    return {
        "product": "",
        "selling_points": [],
        "benefits": [],
        "audience": "",
        "promises": [],
        "source_url": source_url,
        "raw_excerpt": reason,
        "method": "failed",
        "parse_status": "failed",
    }


def analyze_landing(
    brief: str,
    *,
    fetch: FetchFn | None = None,
    pasted_text: str = "",
) -> dict[str, Any] | None:
    """Return landing_page or None when there is no URL and no pasted body."""
    urls = extract_urls(brief or "")
    remainder = URL_RE.sub(" ", brief or "").strip()
    paste = (pasted_text or "").strip()
    if not paste and not urls and len(remainder) >= 200:
        paste = remainder
    if not urls and not paste:
        return None
    if urls:
        url = urls[0]
        logger.info("landing.analysis_started url=%s pasted_text=%s", safe_url(url), bool(paste))
        try:
            html, final = (fetch or safe_fetch)(url)
            if "<" in html:
                result = extract_from_html(html, final or url)
            else:
                result = extract_from_text(html, final or url, method="local_extract")
            logger.info(
                "landing.analysis_completed status=%s method=%s product_chars=%s "
                "selling_points=%s excerpt_chars=%s",
                result.get("parse_status"),
                result.get("method"),
                len(str(result.get("product") or "")),
                len(result.get("selling_points") or []),
                len(str(result.get("raw_excerpt") or "")),
            )
            return result
        except ValueError as exc:
            logger.warning("landing.analysis_failed url=%s error=%s", safe_url(url), clip_text(exc, 240))
            if paste:
                result = extract_from_text(paste, url, method="pasted_text")
                if result["parse_status"] == "ok":
                    result["parse_status"] = "partial"
                result["raw_excerpt"] = f"{exc}\n{result.get('raw_excerpt') or ''}".strip()
                logger.info("landing.analysis_fallback method=pasted_text status=%s", result.get("parse_status"))
                return result
            item = failed_landing(url, str(exc))
            return item
    result = extract_from_text(paste, method="pasted_text")
    logger.info(
        "landing.analysis_completed status=%s method=pasted_text product_chars=%s selling_points=%s",
        result.get("parse_status"),
        len(str(result.get("product") or "")),
        len(result.get("selling_points") or []),
    )
    return result


def landing_prompt_block(landing: dict[str, Any] | None) -> str:
    if not landing:
        return ""
    status = landing.get("parse_status") or ""
    if status == "failed":
        return (
            "落地页未能完整解析，不得把失败结果当成已确认卖点。"
            f"来源：{landing.get('source_url') or '无'}。"
            f"说明：{landing.get('raw_excerpt') or ''}"
        )
    parts = [
        f"落地页解析状态：{status}",
        f"来源：{landing.get('source_url') or '无'}",
        f"产品：{landing.get('product') or '未标明'}",
        f"受众：{landing.get('audience') or '未标明'}",
        "卖点：" + "；".join(landing.get("selling_points") or []) or "卖点：无",
        "利益：" + "；".join(landing.get("benefits") or []) or "利益：无",
        "承诺：" + "；".join(landing.get("promises") or []) or "承诺：无",
    ]
    if status == "partial":
        parts.insert(0, "落地页仅部分解析，提示词里不得声称已完整分析。")
    return "\n".join(parts)
