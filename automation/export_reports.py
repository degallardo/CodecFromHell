import argparse
from pathlib import Path

from azure.data.tables import TableClient

from common import load_json, write_csv


def entity_row(entity: dict) -> dict:
    return {
        "job_id": entity.get("job_id", ""),
        "status": entity.get("status", ""),
        "source_path": entity.get("source_path", ""),
        "source_file_id": entity.get("source_file_id", ""),
        "source_sha256": entity.get("source_sha256", ""),
        "source_size": entity.get("source_size", 0),
        "output_path": entity.get("output_path", ""),
        "output_file_id": entity.get("output_file_id", ""),
        "output_sha256": entity.get("output_sha256", ""),
        "output_size": entity.get("output_size", 0),
        "attempt": entity.get("attempt", 0),
        "worker_id": entity.get("worker_id", ""),
        "last_error": entity.get("last_error", ""),
        "can_delete_source": entity.get("can_delete_source", False),
        "created_at": entity.get("created_at", ""),
        "updated_at": entity.get("updated_at", ""),
    }


def main():
    parser = argparse.ArgumentParser(description="Export conversion reports from Azure Table")
    parser.add_argument("--config", required=True, help="Path to config json")
    parser.add_argument("--batch-id", required=True, help="Batch id")
    parser.add_argument("--out-dir", required=True, help="Output directory for CSV reports")
    args = parser.parse_args()

    config = load_json(args.config)
    table_client = TableClient.from_connection_string(
        conn_str=config["storage_connection_string"],
        table_name=config["table_name"],
    )

    entities = list(table_client.query_entities(query_filter=f"PartitionKey eq '{args.batch_id}'"))

    rows = [entity_row(entity) for entity in entities]
    converted = [row for row in rows if row["status"] in {"converted", "verified"}]
    failed = [row for row in rows if row["status"] in {"failed_retryable", "failed_final"}]
    safe_delete = [row for row in rows if row["status"] == "verified" and bool(row["can_delete_source"])]

    out_dir = Path(args.out_dir)
    fieldnames = list(entity_row({}).keys())

    write_csv(out_dir / "all_jobs.csv", rows, fieldnames)
    write_csv(out_dir / "converted_ok.csv", converted, fieldnames)
    write_csv(out_dir / "failed_keep_264.csv", failed, fieldnames)
    write_csv(out_dir / "safe_delete_264.csv", safe_delete, fieldnames)

    print(f"Exported reports to {out_dir.resolve()}")
    print(f"all_jobs={len(rows)} converted_ok={len(converted)} failed_keep_264={len(failed)} safe_delete_264={len(safe_delete)}")


if __name__ == "__main__":
    main()
