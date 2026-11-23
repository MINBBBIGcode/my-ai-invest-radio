"""Prepare a dated, evidence-first Korean radio bundle from user-owned sources.

Standard-library only. A completed day's bundle is an immutable snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


REPORT_FILE = "report.json"
ROLE_FILES = {"source1": "source1.md", "source2": "source2.md", "source3": "source3.md"}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clean(text: str, limit: int = 6000) -> str:
    text = html.unescape(re.sub(r"<[^>]*>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _public_link(raw: str) -> str:
    """Do not persist feed credentials, query tokens, fragments, or userinfo."""
    try:
        parsed = urlsplit(raw.strip())
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query):
            return ""
        if any(re.fullmatch(r"[A-Za-z0-9_-]{24,}", part) for part in parsed.path.split("/")):
            return ""
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        if parsed.port:
            host += f":{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except ValueError:
        return ""


def _file(base: Path, value: str, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty path")
    path = Path(value)
    path = (path if path.is_absolute() else base / path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{field} file not found: {path}")
    return path


def _local_date(text: str) -> str | None:
    for line in text.splitlines()[:20]:
        match = re.match(r"\s*(?:date|날짜|작성일)\s*:\s*['\"]?(\d{4}-\d{2}-\d{2})", line, re.I)
        if match:
            return date.fromisoformat(match.group(1)).isoformat()
    return None


def _has_body(text: str) -> bool:
    lines = [line for line in text.splitlines()
             if not re.match(r"\s*(?:#|date\s*:|날짜\s*:|작성일\s*:|---\s*$)", line, re.I)]
    return bool(re.sub(r"[\s\W_]", "", " ".join(lines), flags=re.UNICODE))


def _title(text: str, fallback: str) -> str:
    match = re.search(r"(?m)^#\s+(.+?)\s*$", text)
    return _clean(match.group(1), 180) if match else fallback


def _date_value(raw: str, local_zone: ZoneInfo) -> str | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, IndexError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(local_zone).date().isoformat()


def _child_text(node: ET.Element, names: set[str]) -> str:
    for child in node:
        if child.tag.rsplit("}", 1)[-1] in names:
            return "".join(child.itertext()).strip()
    return ""


def _feed_items(raw: bytes, label: str, day: date, max_age: int, zone: ZoneInfo) -> list[dict]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid RSS/Atom XML in {label}: {exc}") from exc
    root_name = root.tag.rsplit("}", 1)[-1]
    if root_name == "rss":
        nodes = root.findall("./channel/item")
        kind = "RSS"
    elif root_name == "feed":
        nodes = [node for node in root if node.tag.rsplit("}", 1)[-1] == "entry"]
        kind = "Atom"
    else:
        raise ValueError(f"Unsupported feed format in {label}")
    items = []
    for node in nodes:
        title = _clean(_child_text(node, {"title"}), 220)
        if not title:
            continue
        published = _date_value(_child_text(node, {"pubDate", "published", "updated"}), zone)
        if not published or not (day - timedelta(days=max_age) <= date.fromisoformat(published) <= day):
            continue
        summary = _clean(_child_text(node, {"description", "summary", "content", "encoded"}), 1600)
        link = _child_text(node, {"link"})
        if kind == "Atom":
            for child in node:
                if child.tag.rsplit("}", 1)[-1] == "link" and child.get("rel", "alternate") == "alternate":
                    link = child.get("href", "")
                    break
        items.append({"title": title, "date": published, "origin": label,
                      "kind": kind, "link": _public_link(link), "excerpt": summary})
    return items


def _read_optional(base: Path, config: dict, field: str) -> tuple[dict | None, bytes | None]:
    value = config.get(field)
    if not value:
        return None, None
    path = _file(base, value, field)
    raw = path.read_bytes()
    body = raw.decode("utf-8-sig")
    if not _has_body(body):
        raise ValueError(f"{field} contains no body text: {path.name}")
    return {"title": _title(body, path.stem), "date": _local_date(body),
            "origin": path.name, "text": body.strip(), "sha256": _digest(raw)}, raw


def _config_signature(config: dict) -> str:
    relevant = {key: config.get(key) for key in (
        "program_name", "language", "timezone", "feeds", "markdown_files",
        "context_file", "briefing_file", "max_items", "max_age_days", "output_dir")}
    return _digest(json.dumps(relevant, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _local_hashes(base: Path, config: dict) -> dict[str, str]:
    values = {}
    for field in ("markdown_files", "context_file", "briefing_file"):
        names = config.get(field) or []
        if field != "markdown_files":
            names = [names] if names else []
        for index, name in enumerate(names):
            path = _file(base, name, field)
            values[f"{field}:{index}"] = _digest(path.read_bytes())
    return values


def _source1(program: str, day: str, items: list[dict], briefing: dict | None) -> str:
    lines = [f"# {program} — 근거 자료", "", f"방송 기준일: {day}",
             "원문에 적힌 제목, 날짜, 발췌만 옮겼습니다. 날짜 없음은 확인되지 않은 날짜입니다.", ""]
    for number, item in enumerate(items, 1):
        lines += [f"## {number}. {item['title']}", f"- 출처: {item['origin']} ({item['kind']})",
                  f"- 자료 날짜: {item['date'] or '확인되지 않음'}"]
        if item["link"]:
            lines.append(f"- 공개 링크: {item['link']}")
        lines += ["", "### 자료 발췌", item["excerpt"] or "제목 외 본문 또는 요약이 제공되지 않았습니다.", ""]
    if briefing:
        lines += ["## 사용자 브리핑", f"- 출처: {briefing['origin']}",
                  f"- 자료 날짜: {briefing['date'] or '확인되지 않음'}", "",
                  briefing["text"], ""]
    return "\n".join(lines)


def _source2(program: str, day: str, items: list[dict], context: dict | None) -> str:
    lines = [f"# {program} — 관점과 확인 범위", "", f"방송 기준일: {day}",
             "각 항목의 근거 범위를 확인하세요. 아래 질문은 사실 주장이나 투자 권유가 아닙니다.", ""]
    if context:
        lines += ["## 청취자 맥락 자료", f"- 출처: {context['origin']}",
                  f"- 자료 날짜: {context['date'] or '확인되지 않음'}", "",
                  context["text"], ""]
    else:
        lines += ["## 청취자 맥락 자료", "제공되지 않았습니다. 청취자 상황에 관한 결론은 내리지 마세요.", ""]
    lines += ["## 기사별 관점과 한계", ""]
    for number, item in enumerate(items, 1):
        scope = ("제목과 제공된 피드 요약" if item["kind"] in {"RSS", "Atom"}
                 else "제공된 로컬 문서 발췌")
        lines += [f"### {number}. {item['title']}",
                  f"- 근거: Source 1의 {number}번, {item['origin']}, {item['date'] or '날짜 미상'}",
                  f"- 확인 가능한 범위: {scope}입니다. 원문 전체, 독립 검증, 후속 변화는 확인하지 않았습니다.",
                  "- 검토할 관점: 누가 영향을 받는지, 반대 자료가 있는지, 기준일 이후 무엇이 달라졌는지 확인하세요."]
        if context:
            lines.append(f"- 맥락 확인: 이 항목과 '{context['title']}'의 관련성을 자료로 입증할 수 있는지 확인하세요.")
        lines.append("")
    return "\n".join(lines)


def _source3(program: str, day: str, items: list[dict], context: dict | None) -> str:
    lines = [f"# {program} — 청취자 질문", "", f"방송 기준일: {day}",
             "진행자는 아래 질문으로 실제 관련성을 점검하세요. 확인되지 않은 답을 단정하지 마세요.", ""]
    for number, item in enumerate(items, 1):
        lines += [f"## {number}. {item['title']}",
                  f"- 참조: Source 1의 {number}번 ({item['origin']}, {item['date'] or '날짜 미상'})"]
        if context:
            lines.append(f"- 맥락 참조: Source 2의 '{context['title']}' ({context['origin']})")
            lines.append(f"- 질문: '{item['title']}'가 이 맥락과 실제로 연결되는가? 연결을 입증하는 자료는 무엇인가?")
        else:
            lines.append(f"- 질문: '{item['title']}'가 청취자의 어떤 선택이나 이해에 도움이 되는가?")
        lines += ["- 질문: 자료 날짜 이후 확인할 변화나 반대 근거가 있는가?", ""]
    return "\n".join(lines)


def prepare(config: dict, base: Path, day: str) -> Path:
    """Return the immutable date directory. Existing dates never refetch feeds."""
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    target_day = date.fromisoformat(day)
    if target_day.isoformat() != day:
        raise ValueError("date must be YYYY-MM-DD")
    base = Path(base).resolve()
    zone_name = config.get("timezone", "Asia/Seoul")
    zone = ZoneInfo(zone_name)
    language = config.get("language", "ko")
    if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language):
        raise ValueError("language must be a language tag such as 'ko' or 'en'")
    program = str(config.get("program_name", "나의 AI 라디오")).strip()
    if not program:
        raise ValueError("program_name must not be empty")
    feeds = config.get("feeds") or []
    markdown_files = config.get("markdown_files") or []
    if not isinstance(feeds, list) or not isinstance(markdown_files, list):
        raise ValueError("feeds and markdown_files must be lists")
    if not feeds and not markdown_files:
        raise ValueError("at least one feed or markdown_file is required")
    max_items = int(config.get("max_items", 6))
    max_age = int(config.get("max_age_days", 7))
    if not 1 <= max_items <= 30 or not 0 <= max_age <= 365:
        raise ValueError("max_items must be 1..30 and max_age_days must be 0..365")
    output_value = config.get("output_dir", "output")
    output_root = (base / output_value).resolve() if not Path(output_value).is_absolute() else Path(output_value).resolve()
    destination = output_root / day
    signature = _config_signature(config)
    if destination.exists():
        report_path = destination / REPORT_FILE
        if not report_path.is_file():
            raise FileExistsError(f"Incomplete dated bundle: {destination}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("config_sha256") != signature:
            raise FileExistsError(f"Dated bundle uses a different config: {destination}")
        if report.get("local_input_sha256") != _local_hashes(base, config):
            raise FileExistsError(f"Local inputs changed after dated bundle was prepared: {destination}")
        for role, name in ROLE_FILES.items():
            path = destination / name
            if not path.is_file() or _digest(path.read_bytes()) != report.get("source_sha256", {}).get(role):
                raise FileExistsError(f"Dated bundle file changed or is missing: {path}")
        return destination
    local_hashes = _local_hashes(base, config)
    items = []
    for value in markdown_files:
        path = _file(base, value, "markdown_files")
        raw = path.read_bytes()
        body = raw.decode("utf-8-sig")
        if not _has_body(body):
            raise ValueError(f"markdown_files contains no body text: {path.name}")
        item_date = _local_date(body)
        if item_date and item_date > day:
            raise ValueError(f"Local document is dated after the requested day: {path.name}")
        items.append({"title": _title(body, path.stem), "date": item_date,
                      "origin": path.name, "kind": "Markdown", "link": "",
                      "excerpt": body.strip()[:6000]})
    for entry in feeds:
        if isinstance(entry, str):
            url, label = entry, urlsplit(entry).hostname or "feed"
        elif isinstance(entry, dict):
            url = entry.get("url", "")
            label = str(entry.get("name") or urlsplit(url).hostname or "feed")
        else:
            raise ValueError("each feed must be a URL or {name, url}")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("feed URL must use http or https")
        try:
            with urlopen(Request(url, headers={"User-Agent": "PortableAIRadio/1.0"}), timeout=15) as response:
                raw = response.read(2_000_001)
        except Exception as exc:
            raise RuntimeError(f"Could not fetch feed '{label}': {type(exc).__name__}") from exc
        if len(raw) > 2_000_000:
            raise ValueError(f"Feed '{label}' exceeds 2 MB")
        items.extend(_feed_items(raw, label, target_day, max_age, zone))
    seen = set()
    eligible = []
    for item in items:
        identity = (item["link"] or item["origin"], item["title"])
        if identity in seen:
            continue
        seen.add(identity)
        eligible.append(item)
    selected = []
    # Newer dates win. Within a date, take one item per origin per pass.
    for item_date in sorted({item["date"] for item in eligible if item["date"]}, reverse=True) + ([None] if any(item["date"] is None for item in eligible) else []):
        buckets: dict[str, list[dict]] = {}
        for item in eligible:
            if item["date"] == item_date:
                buckets.setdefault(item["origin"], []).append(item)
        while any(buckets.values()) and len(selected) < max_items:
            for bucket in buckets.values():
                if bucket and len(selected) < max_items:
                    selected.append(bucket.pop(0))
        if len(selected) >= max_items:
            break
    if not selected:
        raise ValueError("No eligible sources for this date; check feed recency and inputs")
    context, _ = _read_optional(base, config, "context_file")
    briefing, _ = _read_optional(base, config, "briefing_file")
    for field, doc in (("context_file", context), ("briefing_file", briefing)):
        if doc and doc["date"] and doc["date"] > day:
            raise ValueError(f"{field} is dated after the requested day")
    rendered = {
        "source1": _source1(program, day, selected, briefing),
        "source2": _source2(program, day, selected, context),
        "source3": _source3(program, day, selected, context),
    }
    encoded = {role: (body.rstrip() + "\n").encode("utf-8") for role, body in rendered.items()}
    report = {
        "schema_version": 1, "status": "prepared", "date": day, "program_name": program,
        "language": language, "timezone": zone_name, "sources": ROLE_FILES,
        "source_sha256": {role: _digest(raw) for role, raw in encoded.items()},
        "config_sha256": signature, "local_input_sha256": local_hashes,
        "items": [{key: item[key] for key in ("title", "date", "origin", "kind", "link")}
                  for item in selected],
        "context": ({key: context[key] for key in ("title", "date", "origin", "sha256")}
                    if context else None),
        "briefing": ({key: briefing[key] for key in ("title", "date", "origin", "sha256")}
                     if briefing else None),
    }
    output_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{day}-", dir=output_root))
    try:
        for role, raw in encoded.items():
            (temporary / ROLE_FILES[role]).write_bytes(raw)
        (temporary / REPORT_FILE).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.rename(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a dated Korean radio source bundle")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    print(prepare(config, config_path.parent, args.date))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
