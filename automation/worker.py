import argparse
import json
import time
from pathlib import Path
import sys

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError, ResourceModifiedError, ResourceNotFoundError
from azure.data.tables import TableClient
from azure.storage.queue import QueueClient

from common import (
    file_sha256,
    load_json,
    relative_or_name,
    run_template_command,
    to_int,
    utc_now_iso,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def verify_local_output(output_path: Path, verify_command: str | None, job_context: dict) -> tuple[bool, str]:
    if not output_path.exists():
        return False, "output file missing"
    if output_path.stat().st_size <= 0:
        return False, "output file is empty"

    if verify_command:
        result = run_template_command(
            verify_command,
            {
                **job_context,
                "output_path": str(output_path),
            },
        )
        if result["returncode"] != 0:
            return False, f"verify command failed: {result['stderr'] or result['stdout']}"

    return True, "ok"


def update_entity_status(table_client: TableClient, entity: dict, updates: dict) -> dict:
    merged = dict(entity)
    merged.update(updates)
    merged["updated_at"] = utc_now_iso()

    etag = entity.get("etag")
    if etag:
        table_client.update_entity(
            entity=merged,
            mode="MERGE",
            etag=etag,
            match_condition=MatchConditions.IfNotModified,
        )
    else:
        table_client.update_entity(entity=merged, mode="MERGE")

    return table_client.get_entity(partition_key=merged["PartitionKey"], row_key=merged["RowKey"])


def convert_job(entity: dict, config: dict, worker_id: str) -> tuple[bool, str, dict]:
    from hxvs_converter import convert, find_ffmpeg

    source_path = Path(entity["source_path"])
    source_root = Path(config["source_root"]).resolve()
    output_root = Path(config["output_root"]).resolve()

    relative_path = relative_or_name(source_path.resolve(), source_root)
    output_path = (output_root / relative_path).with_suffix(f".{config.get('container', 'mp4')}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg_exe = config.get("ffmpeg_path") or find_ffmpeg()
    if not ffmpeg_exe:
        return False, "ffmpeg not found", {}

    ok = convert(
        input_path=source_path,
        output_path=output_path,
        ffmpeg_exe=ffmpeg_exe,
        container=config.get("container", "mp4"),
        overwrite=bool(config.get("overwrite", False)),
    )
    if not ok:
        return False, "converter returned failure", {}

    verify_ok, verify_msg = verify_local_output(
        output_path=output_path,
        verify_command=config.get("verify_command"),
        job_context={
            "job_id": entity["job_id"],
            "source_path": entity["source_path"],
            "worker_id": worker_id,
        },
    )
    if not verify_ok:
        return False, verify_msg, {"output_path": str(output_path)}

    return True, "converted", {
        "output_path": str(output_path),
        "output_size": output_path.stat().st_size,
        "output_sha256": file_sha256(output_path),
    }


def process_message(queue_client: QueueClient, table_client: TableClient, message, config: dict, worker_id: str):
    try:
        payload = json.loads(message.content)
        batch_id = payload["batch_id"]
        job_id = payload["job_id"]
    except Exception as error:
        print(f"Invalid message payload: {error}")
        queue_client.delete_message(message.id, message.pop_receipt)
        return

    try:
        entity = table_client.get_entity(partition_key=batch_id, row_key=job_id)
    except ResourceNotFoundError:
        print(f"Missing entity for message: {batch_id}/{job_id}. Dropping message.")
        queue_client.delete_message(message.id, message.pop_receipt)
        return

    status = (entity.get("status") or "").lower()
    if status in {"verified", "failed_final"}:
        queue_client.delete_message(message.id, message.pop_receipt)
        print(f"Skip {job_id}: already final status={status}")
        return

    attempt = to_int(entity.get("attempt"), 0) + 1
    max_attempts = to_int(config.get("max_attempts"), 5)

    # Claim
    claim_updates = {
        "status": "claimed",
        "attempt": attempt,
        "worker_id": worker_id,
        "started_at": utc_now_iso(),
        "last_error": "",
    }
    try:
        entity = update_entity_status(table_client, entity, claim_updates)
    except ResourceModifiedError:
        print(f"Race detected on {job_id}, will retry later")
        return

    print(f"[{worker_id}] Processing job={job_id} attempt={attempt}")

    entity = update_entity_status(table_client, entity, {"status": "converting"})

    try:
        ok, detail, artifacts = convert_job(entity, config, worker_id)
    except Exception as error:
        ok = False
        detail = f"exception during convert: {error}"
        artifacts = {}

    if ok:
        post_updates = {
            **artifacts,
            "status": "converted",
            "finished_at": utc_now_iso(),
            "last_error": "",
            "can_delete_source": False,
        }
        entity = update_entity_status(table_client, entity, post_updates)

        uploader_command = config.get("uploader_command")
        if uploader_command:
            upload_result = run_template_command(
                uploader_command,
                {
                    "job_id": entity["job_id"],
                    "batch_id": entity["batch_id"],
                    "source_path": entity["source_path"],
                    "source_file_id": entity.get("source_file_id", ""),
                    "output_path": entity.get("output_path", ""),
                    "worker_id": worker_id,
                },
            )
            if upload_result["returncode"] == 0:
                output_file_id = upload_result["stdout"].splitlines()[0] if upload_result["stdout"] else ""
                entity = update_entity_status(
                    table_client,
                    entity,
                    {
                        "status": "verified",
                        "output_file_id": output_file_id,
                        "can_delete_source": True,
                        "upload_finished_at": utc_now_iso(),
                    },
                )
                queue_client.delete_message(message.id, message.pop_receipt)
                print(f"[{worker_id}] VERIFIED {job_id} output={entity.get('output_path')} file_id={output_file_id}")
                return

            error_text = upload_result["stderr"] or upload_result["stdout"] or "upload command failed"
            if attempt >= max_attempts:
                entity = update_entity_status(
                    table_client,
                    entity,
                    {
                        "status": "failed_final",
                        "last_error": f"upload failure: {error_text}",
                        "finished_at": utc_now_iso(),
                    },
                )
                queue_client.delete_message(message.id, message.pop_receipt)
                print(f"[{worker_id}] FAILED_FINAL {job_id}: {error_text}")
                return

            entity = update_entity_status(
                table_client,
                entity,
                {
                    "status": "failed_retryable",
                    "last_error": f"upload failure: {error_text}",
                    "finished_at": utc_now_iso(),
                },
            )
            print(f"[{worker_id}] RETRY {job_id}: {error_text}")
            return

        # No uploader configured, conversion complete but not yet verified for deletion
        queue_client.delete_message(message.id, message.pop_receipt)
        print(f"[{worker_id}] CONVERTED {job_id} output={entity.get('output_path')} (not verified for deletion)")
        return

    # Conversion failed
    if attempt >= max_attempts:
        entity = update_entity_status(
            table_client,
            entity,
            {
                "status": "failed_final",
                "last_error": detail,
                "finished_at": utc_now_iso(),
            },
        )
        queue_client.delete_message(message.id, message.pop_receipt)
        print(f"[{worker_id}] FAILED_FINAL {job_id}: {detail}")
        return

    update_entity_status(
        table_client,
        entity,
        {
            "status": "failed_retryable",
            "last_error": detail,
            "finished_at": utc_now_iso(),
        },
    )
    print(f"[{worker_id}] RETRYABLE {job_id}: {detail}")


def main():
    parser = argparse.ArgumentParser(description="Distributed conversion worker")
    parser.add_argument("--config", required=True, help="Path to config json")
    parser.add_argument("--once", action="store_true", help="Process one visible message then exit")
    args = parser.parse_args()

    config = load_json(args.config)
    worker_id = config.get("worker_id") or f"worker-{int(time.time())}"

    queue_client = QueueClient.from_connection_string(
        conn_str=config["storage_connection_string"],
        queue_name=config["queue_name"],
    )
    table_client = TableClient.from_connection_string(
        conn_str=config["storage_connection_string"],
        table_name=config["table_name"],
    )

    queue_client.create_queue()
    table_client.create_table_if_not_exists()

    poll_seconds = to_int(config.get("poll_seconds"), 5)
    visibility_timeout = to_int(config.get("visibility_timeout"), 300)

    print(f"Worker started: {worker_id}")

    while True:
        try:
            messages = queue_client.receive_messages(messages_per_page=1, visibility_timeout=visibility_timeout)
            got_any = False
            for message in messages:
                got_any = True
                process_message(queue_client, table_client, message, config, worker_id)
                if args.once:
                    return

            if not got_any:
                if args.once:
                    print("No visible messages.")
                    return
                time.sleep(poll_seconds)

        except (HttpResponseError, Exception) as error:
            print(f"Worker loop error: {error}")
            time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
