#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_MODEL = "openai/chatgpt-5"
OUTPUT_DIR_NAME = "output"

OPENWEBUI_OUTPUT_NAME = "converted-for-open-webui.json"
TXT_OUTPUT_NAME = "chatgpt-conversations-clean.txt"
NDJSON_OUTPUT_NAME = "chatgpt-conversations-normalized.ndjson"
REPORT_OUTPUT_NAME = "conversion-report.json"

SUPPORTED_INPUT_NAMES = ("conversations.json",)


@dataclass(frozen=True)
class NormalizedMessage:
    id: str
    conversation_id: str
    parent_id: Optional[str]
    children_ids: List[str]
    role: str
    content: str
    create_time: Optional[float]
    status: Optional[str]


@dataclass(frozen=True)
class NormalizedConversation:
    id: str
    title: str
    create_time: float
    update_time: float
    messages: List[NormalizedMessage]


def now_ts() -> float:
    return time.time()


def iso_from_ts(ts: Optional[float]) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def stable_uuid_from_text(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return str(uuid.UUID(digest[:32]))


def sanitize_filename_part(value: str, max_len: int = 80) -> str:
    value = value.strip()
    value = re.sub(r"[^\w\s.-]", "", value, flags=re.UNICODE)
    value = re.sub(r"\s+", "_", value)
    return value[:max_len] or "untitled"


def clean_text(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ")

    lines = []
    inside_code_block = False

    for raw_line in text.split("\n"):
        line = raw_line.rstrip()

        if line.strip().startswith("```"):
            inside_code_block = not inside_code_block
            lines.append(line)
            continue

        if inside_code_block:
            lines.append(line)
        else:
            line = re.sub(r"[ \t]+", " ", line).strip()
            lines.append(line)

    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{4,}", "\n\n\n", cleaned)

    return cleaned.strip()


def role_to_label(role: str) -> str:
    mapping = {
        "user": "USER",
        "assistant": "ASSISTANT",
        "system": "SYSTEM",
        "tool": "TOOL",
    }
    return mapping.get(role, role.upper())


def safe_json_dump(path: Path, data: Any, pretty: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        if pretty:
            json.dump(data, f, ensure_ascii=False, indent=2)
        else:
            json.dump(data, f, ensure_ascii=False)


def read_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def find_conversation_files(input_dir: Path) -> List[Path]:
    files: List[Path] = []

    for name in SUPPORTED_INPUT_NAMES:
        candidate = input_dir / name
        if candidate.exists():
            files.append(candidate)

    split_files = sorted(input_dir.glob("conversations-*.json"))
    files.extend(split_files)

    unique_files = []
    seen = set()

    for file in files:
        resolved = file.resolve()
        if resolved not in seen:
            unique_files.append(file)
            seen.add(resolved)

    return unique_files


def extract_content_from_message(message: Dict[str, Any]) -> str:
    content = message.get("content") or {}

    if not isinstance(content, dict):
        return clean_text(str(content))

    content_type = content.get("content_type")

    parts = content.get("parts")
    if isinstance(parts, list):
        extracted_parts: List[str] = []

        for part in parts:
            if part is None:
                continue

            if isinstance(part, str):
                extracted_parts.append(part)
            elif isinstance(part, dict):
                if "text" in part and isinstance(part["text"], str):
                    extracted_parts.append(part["text"])
                else:
                    extracted_parts.append(
                        json.dumps(part, ensure_ascii=False, sort_keys=True)
                    )
            else:
                extracted_parts.append(str(part))

        return clean_text("\n".join(extracted_parts))

    for key in ("text", "result", "value"):
        value = content.get(key)
        if isinstance(value, str):
            return clean_text(value)

    if content_type:
        return clean_text(json.dumps(content, ensure_ascii=False, sort_keys=True))

    return ""


def sort_mapping_nodes_by_time(mapping: Dict[str, Any]) -> List[str]:
    def key_fn(item: Tuple[str, Any]) -> Tuple[float, str]:
        node_id, node = item
        msg = (node or {}).get("message") or {}
        ts = msg.get("create_time")
        if ts is None:
            ts = float("inf")
        return float(ts), str(node_id)
    return [node_id for node_id, _ in sorted(mapping.items(), key=key_fn)]


def normalize_conversation(raw: Dict[str, Any]) -> Optional[NormalizedConversation]:
    conv_id = str(
        raw.get("id")
        or stable_uuid_from_text(json.dumps(raw, ensure_ascii=False, sort_keys=True))
    )
    title = clean_text(raw.get("title") or "Untitled Conversation")
    create_time = float(raw.get("create_time") or now_ts())
    update_time = float(raw.get("update_time") or create_time)
    mapping = raw.get("mapping") or {}
    if not isinstance(mapping, dict) or not mapping:
        return None
    messages: List[NormalizedMessage] = []
    for node_id in sort_mapping_nodes_by_time(mapping):
        node = mapping.get(node_id) or {}
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        author = message.get("author") or {}
        role = author.get("role")
        if not role:
            continue
        if role not in {"system", "user", "assistant", "tool"}:
            continue
        content = extract_content_from_message(message)
        if not content:
            continue
        message_id = str(message.get("id") or node_id)
        parent_id = node.get("parent")
        children_ids = node.get("children") or []
        messages.append(
            NormalizedMessage(
                id=message_id,
                conversation_id=conv_id,
                parent_id=str(parent_id) if parent_id else None,
                children_ids=[str(x) for x in children_ids],
                role=str(role),
                content=content,
                create_time=message.get("create_time"),
                status=message.get("status"),
            )
        )
    if not messages:
        return None
    messages = sorted(
        messages,
        key=lambda m: (
            float(m.create_time) if m.create_time is not None else float("inf"),
            m.id,
        ),
    )
    return NormalizedConversation(
        id=conv_id,
        title=title,
        create_time=create_time,
        update_time=update_time,
        messages=messages,
    )


def load_all_conversations(input_files: Iterable[Path]) -> Tuple[List[NormalizedConversation], Dict[str, Any]]:
    conversations: List[NormalizedConversation] = []

    report = {
        "input_files": [],
        "raw_conversations": 0,
        "normalized_conversations": 0,
        "skipped_conversations": 0,
        "errors": [],
    }

    seen_conv_ids = set()

    for path in input_files:
        file_report = {
            "file": str(path),
            "loaded": 0,
            "normalized": 0,
            "skipped": 0,
        }

        try:
            data = read_json_file(path)

            if not isinstance(data, list):
                raise ValueError("File does not contain a list of conversations.")

            file_report["loaded"] = len(data)
            report["raw_conversations"] += len(data)

            for raw_conv in data:
                if not isinstance(raw_conv, dict):
                    file_report["skipped"] += 1
                    report["skipped_conversations"] += 1
                    continue

                normalized = normalize_conversation(raw_conv)

                if not normalized:
                    file_report["skipped"] += 1
                    report["skipped_conversations"] += 1
                    continue

                if normalized.id in seen_conv_ids:
                    file_report["skipped"] += 1
                    report["skipped_conversations"] += 1
                    continue

                seen_conv_ids.add(normalized.id)
                conversations.append(normalized)
                file_report["normalized"] += 1
                report["normalized_conversations"] += 1

        except Exception as exc:
            report["errors"].append(
                {
                    "file": str(path),
                    "error": str(exc),
                }
            )

        report["input_files"].append(file_report)

    conversations.sort(key=lambda c: (c.create_time, c.title.lower()))

    return conversations, report


def conversation_to_txt(conv: NormalizedConversation) -> str:
    lines: List[str] = []

    lines.append("=" * 100)
    lines.append(f"CONVERSATION_ID: {conv.id}")
    lines.append(f"TITLE: {conv.title}")
    lines.append(f"CREATED_AT_UTC: {iso_from_ts(conv.create_time)}")
    lines.append(f"UPDATED_AT_UTC: {iso_from_ts(conv.update_time)}")
    lines.append("=" * 100)
    lines.append("")

    for msg in conv.messages:
        lines.append("-" * 100)
        lines.append(f"ROLE: {role_to_label(msg.role)}")
        lines.append(f"MESSAGE_ID: {msg.id}")

        if msg.create_time:
            lines.append(f"CREATED_AT_UTC: {iso_from_ts(msg.create_time)}")

        lines.append("-" * 100)
        lines.append(msg.content)
        lines.append("")

    return "\n".join(lines).strip()


def export_txt(conversations: List[NormalizedConversation], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        for index, conv in enumerate(conversations):
            if index:
                f.write("\n\n\n")
            f.write(conversation_to_txt(conv))


def export_ndjson(conversations: List[NormalizedConversation], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        for conv in conversations:
            for msg in conv.messages:
                row = {
                    "conversation_id": conv.id,
                    "conversation_title": conv.title,
                    "conversation_created_at": iso_from_ts(conv.create_time),
                    "conversation_updated_at": iso_from_ts(conv.update_time),
                    "message_id": msg.id,
                    "parent_id": msg.parent_id,
                    "role": msg.role,
                    "created_at": iso_from_ts(msg.create_time),
                    "content": msg.content,
                }
                f.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_openwebui_message(
    msg: NormalizedMessage,
    model: str,
    parent_id: Optional[str],
    children_ids: List[str],
) -> Dict[str, Any]:
    timestamp = int(msg.create_time or now_ts())

    obj: Dict[str, Any] = {
        "id": msg.id,
        "parentId": parent_id,
        "childrenIds": children_ids,
        "role": msg.role,
        "content": msg.content,
        "timestamp": timestamp,
    }

    if msg.role == "assistant":
        obj.update(
            {
                "model": model,
                "modelName": model,
                "modelIdx": 0,
                "userContext": None,
                "lastSentence": msg.content.splitlines()[-1] if msg.content else "",
                "done": True,
                "context": None,
                "info": {
                    "total_duration": 0,
                    "load_duration": 0,
                    "prompt_eval_count": 0,
                    "prompt_eval_duration": 0,
                    "eval_count": 0,
                    "eval_duration": 0,
                },
            }
        )
    elif msg.role == "user":
        obj["models"] = [model]

    return obj


def conversation_to_openwebui_chat(
    conv: NormalizedConversation,
    model: str,
    user_id: str,
) -> Dict[str, Any]:
    messages_list: List[Dict[str, Any]] = []
    messages_dict: Dict[str, Dict[str, Any]] = {}

    for index, msg in enumerate(conv.messages):
        parent_id = conv.messages[index - 1].id if index > 0 else None
        children_ids = [conv.messages[index + 1].id] if index + 1 < len(conv.messages) else []

        owui_msg = build_openwebui_message(
            msg=msg,
            model=model,
            parent_id=parent_id,
            children_ids=children_ids,
        )

        messages_list.append(owui_msg)
        messages_dict[msg.id] = owui_msg

    current_id = messages_list[-1]["id"] if messages_list else None

    created_at = int(conv.create_time)
    updated_at = int(conv.update_time)
    root_timestamp_ms = int(conv.create_time * 1000)

    chat_id = stable_uuid_from_text(f"openwebui:{conv.id}:{conv.title}")

    return {
        "id": chat_id,
        "user_id": user_id,
        "title": conv.title,
        "chat": {
            "id": "",
            "title": conv.title,
            "models": [model],
            "params": {},
            "history": {
                "messages": messages_dict,
                "currentId": current_id,
            },
            "messages": messages_list,
            "tags": ["chatgpt-import"],
            "timestamp": root_timestamp_ms,
            "files": [],
        },
        "updated_at": updated_at,
        "created_at": created_at,
        "share_id": None,
        "archived": False,
        "pinned": False,
        "meta": {
            "source": "chatgpt_export",
            "source_conversation_id": conv.id,
            "converted_at": datetime.now(timezone.utc).isoformat(),
        },
        "folder_id": None,
    }


def export_openwebui(
    conversations: List[NormalizedConversation],
    output_path: Path,
    model: str,
    user_id: str,
) -> None:
    chats = [
        conversation_to_openwebui_chat(conv=conv, model=model, user_id=user_id)
        for conv in conversations
    ]

    safe_json_dump(output_path, chats, pretty=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Converts ChatGPT exports to Open WebUI, clean TXT and NDJSON."
    )

    parser.add_argument(
        "input_dir",
        help="Directory containing conversations.json or conversations-*.json",
    )

    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory. Default: <input_dir>/output",
    )

    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model name to register in Open WebUI. Default: {DEFAULT_MODEL}",
    )

    parser.add_argument(
        "--user-id",
        default=None,
        help="Open WebUI User ID. If omitted, a stable local UUID will be generated.",
    )

    parser.add_argument(
        "--only",
        choices=["all", "openwebui", "txt", "ndjson"],
        default="all",
        help="Controls which outputs to generate. Default: all",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    input_dir = Path(args.input_dir).expanduser().resolve()

    if not input_dir.exists() or not input_dir.is_dir():
        print(f"[ERROR] Directory not found: {input_dir}", file=sys.stderr)
        return 1

    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else input_dir / OUTPUT_DIR_NAME
    )

    input_files = find_conversation_files(input_dir)

    if not input_files:
        print("[ERROR] No conversation files found.", file=sys.stderr)
        print("Expected: conversations.json or conversations-*.json", file=sys.stderr)
        return 1

    print(f"[INFO] Input directory  : {input_dir}")
    print(f"[INFO] Output directory : {output_dir}")
    print(f"[INFO] Files found      : {len(input_files)}")

    conversations, report = load_all_conversations(input_files)

    if not conversations:
        print("[WARN] No conversations were normalized.", file=sys.stderr)

    user_id = args.user_id or stable_uuid_from_text(f"openwebui-user:{input_dir}")

    generated_files: List[str] = []

    if args.only in {"all", "openwebui"}:
        path = output_dir / OPENWEBUI_OUTPUT_NAME
        export_openwebui(
            conversations=conversations,
            output_path=path,
            model=args.model,
            user_id=user_id,
        )
        generated_files.append(str(path))

    if args.only in {"all", "txt"}:
        path = output_dir / TXT_OUTPUT_NAME
        export_txt(conversations=conversations, output_path=path)
        generated_files.append(str(path))

    if args.only in {"all", "ndjson"}:
        path = output_dir / NDJSON_OUTPUT_NAME
        export_ndjson(conversations=conversations, output_path=path)
        generated_files.append(str(path))

    report.update(
        {
            "model": args.model,
            "user_id": user_id,
            "output_dir": str(output_dir),
            "generated_files": generated_files,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    safe_json_dump(output_dir / REPORT_OUTPUT_NAME, report, pretty=True)

    print("")
    print("=" * 80)
    print("REPORT")
    print("=" * 80)
    print(f"Raw conversations         : {report['raw_conversations']}")
    print(f"Normalized conversations  : {report['normalized_conversations']}")
    print(f"Skipped conversations     : {report['skipped_conversations']}")
    print(f"Errors                    : {len(report['errors'])}")
    print("")
    print("Generated files:")
    for file in generated_files:
        print(f"  - {file}")
    print(f"  - {output_dir / REPORT_OUTPUT_NAME}")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
