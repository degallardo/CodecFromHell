import argparse
from pathlib import Path

from azure.data.tables import TableClient

from common import load_json, read_manifest_csv, utc_now_iso


def main():
    parser = argparse.ArgumentParser(description="Mark converted jobs as verified from a CSV")
    parser.add_argument("--config", required=True, help="Path to config json")
    parser.add_argument("--batch-id", required=True, help="Batch id")
    parser.add_argument(
        "--manifest",
        required=True,
        help="CSV with columns: source_path,output_file_id(optional)",
    )
    args = parser.parse_args()

    config = load_json(args.config)
    table_client = TableClient.from_connection_string(
        conn_str=config["storage_connection_string"],
        table_name=config["table_name"],
    )

    rows = read_manifest_csv(args.manifest)
    if not rows:
        print("No rows found in manifest")
        return

    entities = list(table_client.query_entities(query_filter=f"PartitionKey eq '{args.batch_id}'"))
    by_source = {str(Path(item.get("source_path", "")).resolve()): item for item in entities}

    marked = 0
    missing = 0
    for row in rows:
        source_path = str(Path(row["source_path"]).resolve())
        entity = by_source.get(source_path)
        if not entity:
            missing += 1
            print(f"NOT FOUND for source_path={source_path}")
            continue

        entity["status"] = "verified"
        entity["can_delete_source"] = True
        entity["output_file_id"] = row.get("output_file_id", "") or entity.get("output_file_id", "")
        entity["updated_at"] = utc_now_iso()
        entity["upload_finished_at"] = utc_now_iso()

        table_client.update_entity(entity=entity, mode="MERGE")
        marked += 1
        print(f"VERIFIED {entity['RowKey']} {source_path}")

    print(f"Done. Marked={marked}, Missing={missing}, Batch={args.batch_id}")


if __name__ == "__main__":
    main()
