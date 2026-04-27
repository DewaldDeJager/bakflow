"""Full cross-drive comparison: compute_tree_metadata on Bare, then compare everything.

Consolidates checks from final_comparison_v2 and repo_methods into a single script.
Exercises: compute_tree_metadata, count_folders_at_depth, get_folders_at_depth,
get_pending_files, get_parent_entry, get_pruned_ancestor, get_max_depth.
"""

import sqlite3
from src.db.repository import Repository

DB_PATH = "drive_triage.db"

conn = sqlite3.connect(DB_PATH)
conn.execute("PRAGMA foreign_keys = ON")
repo = Repository(conn)

drives = {r[1]: r[0] for r in conn.execute("SELECT id, label FROM drives").fetchall()}
ALL_ID = drives["All"]
BARE_ID = drives["Bare"]
print(f"All:  {ALL_ID}")
print(f"Bare: {BARE_ID}")

# ── compute_tree_metadata on Bare ─────────────────────────────────
print("\nRunning compute_tree_metadata on Bare...")
updated = repo.compute_tree_metadata(BARE_ID)
print(f"Entries updated: {updated}")

# ── Compare count columns ─────────────────────────────────────────
for col in ["child_count", "descendant_file_count", "descendant_folder_count"]:
    row = conn.execute(f"""
        SELECT COUNT(*),
               SUM(CASE WHEN a.{col} = b.{col} THEN 1 ELSE 0 END),
               SUM(CASE WHEN a.{col} != b.{col} THEN 1 ELSE 0 END)
        FROM entries a JOIN entries b ON a.path = b.path
        WHERE a.drive_id = ? AND b.drive_id = ? AND a.entry_type = 'folder'
    """, (ALL_ID, BARE_ID)).fetchone()
    print(f"\n{col}: total={row[0]}  match={row[1]}  mismatch={row[2]}")
    if row[2] and row[2] > 0:
        rows = conn.execute(f"""
            SELECT a.path, a.{col} as all_val, b.{col} as bare_val
            FROM entries a JOIN entries b ON a.path = b.path
            WHERE a.drive_id = ? AND b.drive_id = ? AND a.entry_type = 'folder'
              AND a.{col} != b.{col}
            ORDER BY ABS(a.{col} - b.{col}) DESC
            LIMIT 15
        """, (ALL_ID, BARE_ID)).fetchall()
        for r in rows:
            print(f"    {r[0]}: All={r[1]} Bare={r[2]}")

# ── entry_type consistency ────────────────────────────────────────
print("\nentry_type consistency:")
row = conn.execute("""
    SELECT COUNT(*),
           SUM(CASE WHEN a.entry_type = b.entry_type THEN 1 ELSE 0 END),
           SUM(CASE WHEN a.entry_type != b.entry_type THEN 1 ELSE 0 END)
    FROM entries a JOIN entries b ON a.path = b.path
    WHERE a.drive_id = ? AND b.drive_id = ?
""", (ALL_ID, BARE_ID)).fetchone()
print(f"  total={row[0]}  match={row[1]}  mismatch={row[2]}")
if row[2] and row[2] > 0:
    rows = conn.execute("""
        SELECT a.path, a.entry_type, b.entry_type
        FROM entries a JOIN entries b ON a.path = b.path
        WHERE a.drive_id = ? AND b.drive_id = ? AND a.entry_type != b.entry_type
        LIMIT 10
    """, (ALL_ID, BARE_ID)).fetchall()
    for r in rows:
        print(f"    {r[0]}: All={r[1]} Bare={r[2]}")

# ── File count columns still NULL ─────────────────────────────────
print("\nFile count columns (should be 0 non-NULL):")
for label, did in [("All", ALL_ID), ("Bare", BARE_ID)]:
    row = conn.execute("""
        SELECT SUM(CASE WHEN child_count IS NOT NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN descendant_file_count IS NOT NULL THEN 1 ELSE 0 END)
        FROM entries WHERE drive_id = ? AND entry_type = 'file'
    """, (did,)).fetchone()
    print(f"  {label}: cc={row[0]} df={row[1]}")

# ── get_max_depth ─────────────────────────────────────────────────
all_max = repo.get_max_depth(ALL_ID)
bare_max = repo.get_max_depth(BARE_ID)
print(f"\nget_max_depth: All={all_max} Bare={bare_max} match={all_max == bare_max}")

# ── count_folders_at_depth (every level) ──────────────────────────
print("\ncount_folders_at_depth per level:")
print(f"{'depth':>5}  {'All':>6}  {'Bare':>6}  {'match':>5}")
max_d = max(all_max, bare_max)
for d in range(max_d + 1):
    a = repo.count_folders_at_depth(ALL_ID, d)
    b = repo.count_folders_at_depth(BARE_ID, d)
    print(f"{d:>5}  {a:>6}  {b:>6}  {a == b!s:>5}")

# ── get_folders_at_depth — ordering and path consistency ──────────
print("\nget_folders_at_depth ordering + path consistency (depths 0-3):")
for d in range(4):
    folders_all = repo.get_folders_at_depth(ALL_ID, d)
    folders_bare = repo.get_folders_at_depth(BARE_ID, d)

    # Path consistency
    a_paths = {f.path for f in folders_all}
    b_paths = {f.path for f in folders_bare}
    only_all = a_paths - b_paths
    only_bare = b_paths - a_paths
    if only_all or only_bare:
        print(f"  depth {d}: PATH MISMATCH  only_all={only_all}  only_bare={only_bare}")
    else:
        print(f"  depth {d}: {len(a_paths)} folders, paths match")

    # Ordering (descending by descendant_file_count)
    for label, folders in [("All", folders_all), ("Bare", folders_bare)]:
        counts = [f.descendant_file_count or 0 for f in folders]
        is_sorted = all(a >= b for a, b in zip(counts, counts[1:]))
        print(f"    {label} sorted DESC: {is_sorted}")

# ── get_folders_at_depth with exclude_pruned ──────────────────────
print("\nget_folders_at_depth (exclude_pruned=True) — entry counts:")
print(f"{'depth':>5}  {'All':>6}  {'Bare':>6}  {'match':>5}")
for d in [0, 1, 2, 3]:
    a = repo.get_folders_at_depth(ALL_ID, d, exclude_pruned=True)
    b = repo.get_folders_at_depth(BARE_ID, d, exclude_pruned=True)
    print(f"{d:>5}  {len(a):>6}  {len(b):>6}  {len(a) == len(b)!s:>5}")

# ── get_folders_at_depth depth=1 detail ───────────────────────────
print("\nget_folders_at_depth depth=1 detail (first 5):")
for label, did in [("All", ALL_ID), ("Bare", BARE_ID)]:
    folders = repo.get_folders_at_depth(did, 1)
    print(f"  {label}: {len(folders)} folders")
    for f in folders[:5]:
        print(f"    {f.path:<60} desc_files={f.descendant_file_count}")

# ── get_pending_files ─────────────────────────────────────────────
print("\nget_pending_files (batch_size=100):")
for label, did in [("All", ALL_ID), ("Bare", BARE_ID)]:
    pf = repo.get_pending_files(did, batch_size=100)
    all_files = all(e.entry_type == "file" for e in pf)
    all_unclassified = all(e.classification_status in ("unclassified", "needs_reclassification") for e in pf)
    all_null_counts = all(e.child_count is None and e.descendant_file_count is None for e in pf)
    print(f"  {label}: {len(pf)} files, all_files={all_files}, all_unclassified={all_unclassified}, null_counts={all_null_counts}")

# ── get_parent_entry (spot checks) ────────────────────────────────
print("\nget_parent_entry spot checks:")
test_cases = [
    ("F:/SteamLibrary", "F:/"),
    ("F:/", None),
]
for path, expected_parent_path in test_cases:
    row = conn.execute(
        "SELECT parent_path FROM entries WHERE drive_id = ? AND path = ? LIMIT 1",
        (ALL_ID, path),
    ).fetchone()
    if row is None:
        print(f"  {path}: entry not found")
        continue
    pp = row[0]
    if pp is None:
        print(f"  {path}: parent_path=None (root)")
        continue
    parent_all = repo.get_parent_entry(ALL_ID, pp)
    parent_bare = repo.get_parent_entry(BARE_ID, pp)
    a_name = parent_all.name if parent_all else None
    b_name = parent_bare.name if parent_bare else None
    print(f"  {path}: parent_path={pp!r}  All={a_name}  Bare={b_name}  match={a_name == b_name}")

# ── get_pruned_ancestor (no decisions yet) ────────────────────────
print("\nget_pruned_ancestor (expect None):")
sample = conn.execute("""
    SELECT path FROM entries WHERE drive_id = ? AND depth = 3 LIMIT 3
""", (ALL_ID,)).fetchall()
for (p,) in sample:
    a = repo.get_pruned_ancestor(ALL_ID, p)
    b = repo.get_pruned_ancestor(BARE_ID, p)
    print(f"  {p}: All={a} Bare={b}")

conn.close()
print("\nAll checks complete.")
