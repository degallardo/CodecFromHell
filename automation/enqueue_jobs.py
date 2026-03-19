import argparse
import json
import uuid
from pathlib import Path

from azure.data.tables import TableClient
from azure.storage.queue import QueueClient

from common import load_json, read_manifest_csv, to_int, utc_now_iso


def build_entity(batch_id: str, job_id: str, row: dict) -> dict:
    source_path = Path(row["source_path"])
    source_size = to_int(row.get("source_size"), default=source_path.stat().st_size if source_path.exists() else 0)
    return {
        "PartitionKey": batch_id,
        "RowKey": job_id,
        "job_id": job_id,
        "batch_id": batch_id,
        "source_path": str(source_path),
        "source_file_id": row.get("source_file_id") or "",
        "source_sha256": row.get("source_sha256") or "",
        "source_size": source_size,
        "status": "pending",
        "attempt": 0,
        "worker_id": "",
        "last_error": "",
        "output_path": "",
        "output_file_id": "",
        "output_sha256": "",
        "output_size": 0,
        "can_delete_source": False,
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }


def main():
    parser = argparse.ArgumentParser(description="Enqueue .264 jobs into Azure Queue + Table")
    parser.add_argument("--config", required=True, help="Path to config json")
    parser.add_argument("--manifest", required=True, help="CSV path with column: source_path")
    parser.add_argument("--batch-id", default=None, help="Override batch_id from config")
    args = parser.parse_args()

    config = load_json(args.config)
    batch_id = args.batch_id or config["batch_id"]

    rows = read_manifest_csv(args.manifest)
    if not rows:
        print("No rows in manifest. Nothing enqueued.")
        return

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

    enqueued = 0
    skipped = 0

    for row in rows:
        source_path = Path(row["source_path"])
        if not source_path.exists():
            skipped += 1
            print(f"SKIP missing file: {source_path}")
            continue

        job_id = str(uuid.uuid4())
        entity = build_entity(batch_id, job_id, row)

        table_client.upsert_entity(entity=entity, mode="Replace")
        message = {"batch_id": batch_id, "job_id": job_id}
        queue_client.send_message(json.dumps(message))

        enqueued += 1
        print(f"ENQUEUED {job_id} -> {source_path}")

    print(f"Done. Enqueued={enqueued}, Skipped={skipped}, Batch={batch_id}")


if __name__ == "__main__":
    main()
