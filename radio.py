"""Resume-safe, local NotebookLM Audio Overview runner for the portable radio kit.

Requires notebooklm-py==0.8.6 on PATH, an authenticated local CLI profile, and
ffprobe. The CLI is an unofficial client; keep its version pinned and review
the commands before upgrading it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CLI_VERSION = "0.8.6"
SOURCE_NAMES = ("source1.md", "source2.md", "source3.md")
COMMAND_TIMEOUT = 120
SOURCE_TIMEOUT = 600
AUDIO_TIMEOUT = 1200


class RadioError(RuntimeError):
    """A safe stop that leaves the saved state available for a later run."""


def save_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RadioError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RadioError(f"Expected a JSON object in {path}")
    return value


def config_value(config: dict[str, Any], name: str) -> str:
    value = config.get(name)
    if not isinstance(value, str) or not value.strip():
        raise RadioError(f"Config needs a nonempty {name} string")
    return value.strip()


def positive_seconds(config: dict[str, Any], name: str, default: int, maximum: int) -> int:
    value = config.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise RadioError(f"{name} must be an integer between 1 and {maximum}")
    return value


def cli(argv: list[str], *, timeout: int = COMMAND_TIMEOUT) -> Any:
    """Run one pinned CLI command. Mutations are always journaled by the caller."""
    try:
        result = subprocess.run(
            ["notebooklm", *argv], capture_output=True, text=True,
            encoding="utf-8", timeout=timeout, check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except FileNotFoundError as exc:
        raise RadioError("Install notebooklm-py==0.8.6 and put notebooklm on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RadioError(f"NotebookLM command timed out: {' '.join(argv[:2])}") from exc
    if result.returncode:
        # CLI errors can contain source text, cookies, or account details.
        raise RadioError(
            f"NotebookLM command failed ({result.returncode}): {' '.join(argv[:2])}. "
            "Check the CLI locally; the saved pending step prevents a blind retry."
        )
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise RadioError(f"NotebookLM returned non-JSON output: {' '.join(argv[:2])}") from exc


def check_cli_version() -> None:
    try:
        result = subprocess.run(
            ["notebooklm", "--version"], capture_output=True, text=True,
            encoding="utf-8", timeout=15, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RadioError("Install notebooklm-py==0.8.6 and put notebooklm on PATH") from exc
    version = re.search(r"\b(\d+\.\d+\.\d+)\b", result.stdout)
    if result.returncode or not version or version.group(1) != CLI_VERSION:
        raise RadioError(f"This kit requires notebooklm-py=={CLI_VERSION}")


def check_auth() -> None:
    # Passive mode will not run refresh hooks or change the user's cookie store.
    cli(["auth", "check", "--test", "--passive", "--json"], timeout=45)


def rows(value: Any, key: str) -> list[dict[str, Any]]:
    items = value.get(key) if isinstance(value, dict) else value
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise RadioError(f"Unexpected NotebookLM {key} response")
    return items


def remote_id(value: Any, *keys: str) -> str:
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for nested in ("notebook", "source", "artifact"):
            candidate = value.get(nested)
            if isinstance(candidate, dict):
                result = remote_id(candidate, *keys)
                if result:
                    return result
    return ""


def require_id(value: Any, *keys: str) -> str:
    result = remote_id(value, *keys)
    if not result:
        raise RadioError("NotebookLM response had no usable ID; inspect pending state before retrying")
    return result


def source_fingerprints(directory: Path) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for name in SOURCE_NAMES:
        path = directory / name
        if not path.is_file() or path.stat().st_size == 0:
            raise RadioError(f"Missing or empty prepared source: {path}")
        fingerprints[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return fingerprints


def verify_bundle(directory: Path, config: dict[str, Any], day: str) -> dict[str, str]:
    report = load_json(directory / "report.json")
    if (report.get("schema_version") != 1 or report.get("status") != "prepared"
            or report.get("date") != day
            or report.get("program_name") != config_value(config, "program_name")
            or report.get("language") != config_value(config, "language")
            or report.get("timezone") != config_value(config, "timezone")):
        raise RadioError("Prepared bundle does not match this station and date")
    actual = source_fingerprints(directory)
    recorded = report.get("source_sha256")
    if not isinstance(recorded, dict) or any(
        actual[name] != recorded.get(name.removesuffix(".md")) for name in SOURCE_NAMES
    ):
        raise RadioError("Prepared bundle differs from its report.json hashes")
    return actual


def prepared_directory(config: dict[str, Any], base: Path, day: str,
                       *, validate_inputs: bool) -> Path:
    output_dir = Path(config_value(config, "output_dir"))
    if not output_dir.is_absolute():
        output_dir = base / output_dir
    expected = (output_dir / day).resolve()
    if expected.exists() and not validate_inputs:
        verify_bundle(expected, config, day)
        return expected
    from prepare import prepare  # sibling's contract; import only when preparation is needed

    try:
        actual = Path(prepare(config, base, day))
    except (ValueError, RuntimeError, FileExistsError) as exc:
        raise RadioError(f"Could not prepare {day}: {exc}") from exc
    if not actual.is_absolute():
        actual = base / actual
    if actual.resolve() != expected:
        raise RadioError(f"prepare() must return the configured date directory: {expected}")
    verify_bundle(expected, config, day)
    return expected


def mark_pending(state: dict[str, Any], path: Path, action: str, **details: Any) -> None:
    state["pending"] = {"action": action, **details}
    save_json(path, state)


def commit(state: dict[str, Any], path: Path) -> None:
    state.pop("pending", None)
    save_json(path, state)


def list_notebooks() -> list[dict[str, Any]]:
    return rows(cli(["list", "--json"]), "notebooks")


def list_sources(notebook_id: str) -> list[dict[str, Any]]:
    return rows(cli(["source", "list", "-n", notebook_id, "--json"]), "sources")


def list_artifacts(notebook_id: str) -> list[dict[str, Any]]:
    return rows(cli(["artifact", "list", "-n", notebook_id, "--type", "audio", "--json"]), "artifacts")


def reconcile_pending(state: dict[str, Any], path: Path) -> None:
    pending = state.get("pending")
    if not pending:
        return
    if not isinstance(pending, dict):
        raise RadioError("Invalid pending state")
    action = pending.get("action")
    if action == "create":
        matches = [item for item in list_notebooks() if item.get("title") == pending.get("title")]
        if len(matches) == 1:
            state["notebook_id"] = require_id(matches[0], "id", "notebook_id")
        else:
            raise RadioError("Notebook creation is uncertain; inspect the account before retrying")
    elif action == "add_source":
        matches = [item for item in list_sources(state["notebook_id"])
                   if item.get("title") == pending.get("title")]
        if len(matches) == 1:
            state.setdefault("source_ids", {})[pending["name"]] = require_id(
                matches[0], "id", "source_id"
            )
        else:
            raise RadioError("Source upload is uncertain; inspect the notebook before retrying")
    elif action == "generate":
        before = pending.get("before_ids")
        if not isinstance(before, list):
            raise RadioError("Missing pre-generation artifact snapshot")
        new_items = [item for item in list_artifacts(state["notebook_id"])
                     if remote_id(item, "id", "artifact_id", "task_id") not in before]
        if len(new_items) == 1:
            state["artifact_id"] = require_id(new_items[0], "id", "artifact_id", "task_id")
        else:
            raise RadioError("Audio generation is uncertain; inspect the notebook before retrying")
    else:
        raise RadioError(f"Unknown pending action: {action}")
    commit(state, path)


def ensure_notebook(state: dict[str, Any], path: Path, title: str) -> str:
    if state.get("notebook_id"):
        return state["notebook_id"]
    # A matching notebook with no local receipt may belong to a different run.
    if any(item.get("title") == title for item in list_notebooks()):
        raise RadioError("A notebook with this date/title already exists without local state")
    mark_pending(state, path, "create", title=title)
    created = cli(["create", title, "--json"])
    state["notebook_id"] = require_id(created, "id", "notebook_id")
    commit(state, path)
    return state["notebook_id"]


def ensure_sources(state: dict[str, Any], path: Path, directory: Path,
                   source_timeout: int) -> list[str]:
    notebook_id = state["notebook_id"]
    ids = state.setdefault("source_ids", {})
    for name in SOURCE_NAMES:
        if name not in ids:
            title = f"{state['day']} {name} {state['fingerprints'][name][:12]}"
            if any(item.get("title") == title for item in list_sources(notebook_id)):
                raise RadioError(f"Remote source {title} exists without local state")
            mark_pending(state, path, "add_source", name=name, title=title)
            added = cli(["source", "add", str(directory / name), "--type", "file", "--title", title,
                         "-n", notebook_id, "--json"], timeout=COMMAND_TIMEOUT)
            ids[name] = require_id(added, "id", "source_id")
            commit(state, path)
        cli(["source", "wait", ids[name], "-n", notebook_id,
             "--timeout", str(source_timeout), "--json"], timeout=source_timeout + 30)
        source = cli(["source", "get", ids[name], "-n", notebook_id, "--json"])
        if not isinstance(source, dict) or source.get("status") != "ready":
            raise RadioError(f"Source {name} is not confirmed ready")
    selected = [ids[name] for name in SOURCE_NAMES]
    if len(set(selected)) != len(SOURCE_NAMES):
        raise RadioError("The three prepared sources do not have distinct remote IDs")
    remote_ids = {require_id(item, "id", "source_id") for item in list_sources(notebook_id)}
    if remote_ids != set(selected):
        raise RadioError("Notebook sources differ from this run's three prepared files")
    return selected


def ensure_artifact(state: dict[str, Any], path: Path, config: dict[str, Any],
                    source_ids: list[str], audio_timeout: int) -> str:
    notebook_id = state["notebook_id"]
    if not state.get("artifact_id"):
        before = [require_id(item, "id", "artifact_id", "task_id")
                  for item in list_artifacts(notebook_id)]
        if before:
            raise RadioError("Notebook already has audio without this run's generation receipt")
        mark_pending(state, path, "generate", before_ids=before)
        args = ["generate", "audio"]
        instructions = config.get("audio_instructions")
        if instructions:
            args.append(instructions.strip())
        args += ["-n", notebook_id]
        for source_id in source_ids:
            args += ["-s", source_id]
        args += ["--format", "deep-dive", "--language", config_value(config, "language"),
                 "--no-wait", "--retry", "0", "--json"]
        generated = cli(args)
        state["artifact_id"] = require_id(generated, "task_id", "artifact_id", "id")
        commit(state, path)
    artifact_id = state["artifact_id"]
    cli(["artifact", "wait", artifact_id, "-n", notebook_id,
         "--timeout", str(audio_timeout), "--json"], timeout=audio_timeout + 30)
    artifact = cli(["artifact", "get", artifact_id, "-n", notebook_id, "--json"])
    if not isinstance(artifact, dict) or artifact.get("status") not in ("completed", "complete", "ready"):
        raise RadioError("Audio artifact is not confirmed complete")
    return artifact_id


def verify_audio(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 1024:
        raise RadioError(f"Audio file is missing or too small: {path}")
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type", "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
        )
    except FileNotFoundError as exc:
        raise RadioError("Install FFmpeg (ffprobe) to verify downloaded audio") from exc
    except subprocess.TimeoutExpired as exc:
        raise RadioError("ffprobe timed out") from exc
    try:
        probe = json.loads(result.stdout)
        duration = float(probe.get("format", {}).get("duration", 0))
        has_audio = any(s.get("codec_type") == "audio" for s in probe.get("streams", []))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RadioError(f"Cannot inspect audio file: {path}") from exc
    if result.returncode or duration <= 1 or not has_audio:
        raise RadioError(f"Downloaded file is not valid audio: {path}")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verified_saved_audio(state: dict[str, Any], directory: Path) -> Path | None:
    final = directory / "radio.m4a"
    if not final.exists():
        return None
    expected = state.get("audio_sha256")
    if (expected is None and state.get("audio_pending_artifact_id") == state.get("artifact_id")
            and isinstance(state.get("audio_pending_sha256"), str)):
        expected = state["audio_pending_sha256"]
        if file_sha256(final) == expected:
            verify_audio(final)
            state["audio_sha256"] = expected
            state["audio_path"] = str(final)
            return final
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise RadioError("Existing audio has no artifact-bound local checksum receipt")
    if not state.get("artifact_id"):
        raise RadioError("Existing audio has no artifact ID receipt")
    try:
        verify_audio(final)
        if file_sha256(final) == expected:
            return final
    except RadioError:
        pass
    damaged = directory / "radio.damaged.m4a"
    if damaged.exists():
        raise RadioError("Damaged audio needs manual review; quarantine path already exists")
    final.rename(damaged)
    return None


def download_audio(state: dict[str, Any], path: Path, directory: Path) -> Path:
    final = directory / "radio.m4a"
    saved = verified_saved_audio(state, directory)
    if saved:
        save_json(path, state)
        return saved
    partial = directory / "radio.part.m4a"
    if partial.exists():
        # A previous process may have stopped during or just after download.
        # Re-fetch by exact artifact ID; do not adopt a merely playable partial.
        partial.unlink()
    cli(["download", "audio", str(partial), "-a", state["artifact_id"],
         "-n", state["notebook_id"], "--no-clobber", "--json"], timeout=300)
    verify_audio(partial)
    if final.exists():
        raise RadioError("Audio output appeared during download; inspect before replacing it")
    state["audio_pending_artifact_id"] = state["artifact_id"]
    state["audio_pending_sha256"] = file_sha256(partial)
    save_json(path, state)
    os.replace(partial, final)
    state["audio_sha256"] = state.pop("audio_pending_sha256")
    state.pop("audio_pending_artifact_id", None)
    state["audio_path"] = str(final)
    save_json(path, state)
    return final


def run(config_path: Path, day: str | None = None) -> dict[str, str]:
    config_path = config_path.resolve()
    config = load_json(config_path)
    base = config_path.parent
    try:
        timezone = ZoneInfo(config_value(config, "timezone"))
    except ZoneInfoNotFoundError as exc:
        raise RadioError("Timezone data is unavailable; install tzdata or use a valid timezone") from exc
    if day is None:
        day = datetime.now(timezone).date().isoformat()
    try:
        if datetime.strptime(day, "%Y-%m-%d").date().isoformat() != day:
            raise ValueError(day)
    except ValueError as exc:
        raise RadioError("--date must be YYYY-MM-DD") from exc
    program_name = config_value(config, "program_name")
    language = config_value(config, "language")
    if not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language):
        raise RadioError("language must be a language tag such as ko or en")
    instructions = config.get("audio_instructions")
    if instructions is not None and not isinstance(instructions, str):
        raise RadioError("audio_instructions must be a string")
    if shutil.which("ffprobe") is None:
        raise RadioError("Install FFmpeg (ffprobe) before starting remote work")
    source_timeout = positive_seconds(config, "source_timeout", SOURCE_TIMEOUT, 3600)
    audio_timeout = positive_seconds(config, "audio_timeout", AUDIO_TIMEOUT, 7200)
    state_dir = base / ".radio-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / f"{day}.json"
    lock_path = state_dir / f"{day}.lock"
    try:
        fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RadioError(f"Another run holds {lock_path}; inspect its process before removing a stale lock") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as lock:
            lock.write(f"pid={os.getpid()}\n")
        directory = prepared_directory(config, base, day,
                                       validate_inputs=not state_path.exists())
        fingerprints = verify_bundle(directory, config, day)
        if state_path.exists():
            state = load_json(state_path)
            if state.get("schema") != 1 or state.get("day") != day:
                raise RadioError("Saved state has an unsupported schema or date")
            if state.get("fingerprints") != fingerprints:
                raise RadioError("Prepared sources changed after this run began; preserve the original bundle")
        else:
            state = {"schema": 1, "day": day, "fingerprints": fingerprints, "source_ids": {}}
            save_json(state_path, state)
        if state.get("audio_path") and not state.get("pending"):
            saved_audio = verified_saved_audio(state, directory)
            if saved_audio and saved_audio.resolve() == (directory / "radio.m4a").resolve():
                save_json(state_path, state)
                return {"date": day, "audio_path": str(saved_audio),
                        "listen_url": saved_audio.as_uri(),
                        "notebook_url": f"https://notebooklm.google.com/notebook/{state['notebook_id']}",
                        "notebook_id": state["notebook_id"], "artifact_id": state["artifact_id"]}
        check_cli_version()
        check_auth()
        reconcile_pending(state, state_path)
        notebook_id = ensure_notebook(state, state_path, f"{program_name} {day}")
        source_ids = ensure_sources(state, state_path, directory, source_timeout)
        artifact_id = ensure_artifact(state, state_path, config, source_ids, audio_timeout)
        audio = download_audio(state, state_path, directory)
        return {"date": day, "audio_path": str(audio), "listen_url": audio.as_uri(),
                "notebook_url": f"https://notebooklm.google.com/notebook/{notebook_id}",
                "notebook_id": notebook_id, "artifact_id": artifact_id}
    finally:
        lock_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="prepare, generate, and download one day's audio")
    run_parser.add_argument("--config", type=Path, required=True)
    run_parser.add_argument("--date")
    args = parser.parse_args(argv)
    try:
        result = run(args.config, args.date)
    except (RadioError, OSError) as exc:
        print(f"radio: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
