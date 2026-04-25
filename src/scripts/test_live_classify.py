#!/usr/bin/env python3
"""Live integration test: classify a small batch against a real Ollama server.

Usage:
    python test_live_classify.py [--batch-size 10] [--base-url http://192.168.8.187:11434] [--model llama3.2]

This uses a COPY of the production database so no real data is modified.
"""

import argparse
import asyncio
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from src.classifier.batch import BatchClassifier, BatchResult
from src.classifier.ollama_provider import OllamaProvider
from src.classifier.provider import ClassifierConfig
from src.db.repository import Repository


def print_result(result: BatchResult, repo: Repository, drive_id: str) -> None:
    """Pretty-print classification results."""
    print("\n" + "=" * 70)
    print("BATCH CLASSIFICATION RESULTS")
    print("=" * 70)
    print(f"  Folders classified: {result.folders_classified}")
    print(f"  Folders failed:     {result.folders_failed}")
    print(f"  Files classified:   {result.files_classified}")
    print(f"  Files failed:       {result.files_failed}")
    if result.errors:
        print(f"  Errors:")
        for e in result.errors:
            print(f"    - {e}")

    # Show the classified entries
    conn = repo._conn
    rows = conn.execute(
        "SELECT path, entry_type, file_class, folder_purpose, confidence, "
        "classification_reasoning, priority_review "
        "FROM entries WHERE drive_id = ? AND classification_status = 'ai_classified' "
        "ORDER BY confidence ASC",
        (drive_id,),
    ).fetchall()

    if rows:
        print(f"\n{'─' * 70}")
        print(f"CLASSIFIED ENTRIES ({len(rows)} total)")
        print(f"{'─' * 70}")
        for path, etype, fclass, fpurpose, conf, reasoning, priority in rows:
            label = fpurpose if etype == "folder" else fclass
            flag = " ⚠️  PRIORITY REVIEW" if priority else ""
            print(f"\n  [{etype.upper():6s}] {path}")
            print(f"    Classification: {label}")
            print(f"    Confidence:     {conf:.2f}{flag}")
            print(f"    Reasoning:      {reasoning}")


async def run(
    base_url: str, model: str, batch_size: int, db_path: str
) -> None:
    """Run a live classification batch against Ollama."""

    # Work on a temp copy so we don't touch the real DB
    tmp_dir = tempfile.mkdtemp(prefix="triage_live_test_")
    tmp_db = Path(tmp_dir) / "test_copy.db"
    print(f"Copying database to {tmp_db} ...")
    shutil.copy2(db_path, tmp_db)

    conn = sqlite3.connect(str(tmp_db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    repo = Repository(conn)

    # Get the drive
    drives = repo.list_drives()
    if not drives:
        print("No drives found in database!")
        return

    drive = drives[0]
    total = repo.count_entries_by_drive(drive.id)
    unclassified = conn.execute(
        "SELECT COUNT(*) FROM entries WHERE drive_id=? AND classification_status='unclassified'",
        (drive.id,),
    ).fetchone()[0]

    print(f"\nDrive: {drive.label} ({drive.id[:12]}...)")
    print(f"Total entries: {total:,}")
    print(f"Unclassified:  {unclassified:,}")
    print(f"\nOllama server: {base_url}")
    print(f"Model:         {model}")
    print(f"Batch size:    {batch_size}")

    # Check Ollama connectivity first (use sync client for the check)
    print(f"\nChecking Ollama connectivity...")
    try:
        import ollama as ollama_sdk
        client = ollama_sdk.Client(host=base_url)
        models_resp = client.list()
        available = [m.model for m in models_resp.models]
        print(f"  Connected! Available models: {', '.join(available)}")
        if not any(model in m for m in available):
            print(f"  ⚠️  Model '{model}' not found. Available: {available}")
            print(f"  Will attempt anyway (Ollama may pull it)...")
    except Exception as e:
        print(f"  ❌ Cannot connect to Ollama at {base_url}: {e}")
        conn.close()
        return

    provider = OllamaProvider(model=model, base_url=base_url)

    # Run classification
    config = ClassifierConfig(
        provider="ollama",
        model=model,
        base_url=base_url,
        confidence_threshold=0.7,
        batch_size=batch_size,
    )
    classifier = BatchClassifier(
        provider=provider, repo=repo, conn=conn, config=config
    )

    print(f"\nClassifying batch of {batch_size} entries...")
    print("(This may take a minute depending on model speed)\n")

    result = await classifier.classify_batch(drive.id, batch_size=batch_size)
    print_result(result, repo, drive.id)

    conn.close()
    print(f"\nTemp database at: {tmp_db}")
    print("(Safe to delete — your real database was not modified)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live test: classify a batch against a real Ollama server"
    )
    parser.add_argument(
        "--base-url",
        default="http://192.168.8.187:11434",
        help="Ollama server URL (default: http://192.168.8.187:11434)",
    )
    parser.add_argument(
        "--model",
        default="llama3.2",
        help="Ollama model name (default: llama3.2)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of entries to classify (default: 10)",
    )
    parser.add_argument(
        "--db",
        default="drive_triage.db",
        help="Path to the database (default: drive_triage.db)",
    )
    args = parser.parse_args()

    asyncio.run(run(args.base_url, args.model, args.batch_size, args.db))


if __name__ == "__main__":
    main()
