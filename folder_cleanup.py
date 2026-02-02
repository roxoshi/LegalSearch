import os

# --- Configuration ---
json_root_folder = 'backend/.data/ingestion_data'  # Path to your JSON files (supports subfolders)
pdf_folder = 'backend/.data/gst_pdfs'       # Path to your PDF files
dry_run = False                # Set to False to actually delete files

def cleanup_pdfs():
    # 1. Collect all JSON filenames (without extension)
    # Using a set for O(1) lookup speed
    existing_json_names = set()
    
    print(f"Scanning {json_root_folder} for JSON files...")
    for root, dirs, files in os.walk(json_root_folder):
        for file in files:
            if file.endswith('.json'):
                # Take the filename without the .json extension
                name_key = os.path.splitext(file)[0]
                existing_json_names.add(name_key)

    print(f"Found {len(existing_json_names)} unique JSON references.")

    # 2. Check PDF folder and delete non-matches
    count_deleted = 0
    count_kept = 0

    print(f"Checking {pdf_folder}...")
    for file in os.listdir(pdf_folder):
        if file.endswith('.pdf'):
            base_name = os.path.splitext(file)[0]
            if base_name.endswith('_EN'):
                pdf_name_key = base_name[:-3]
            else:
                pdf_name_key = base_name
            if pdf_name_key in existing_json_names:
                count_kept += 1
            else:
                file_path = os.path.join(pdf_folder, file)
                if dry_run:
                    print(f"[DRY RUN] Would delete: {file}")
                else:
                    os.remove(file_path)
                    print(f"Deleted: {file}")
                count_deleted += 1

    # Final Report
    status = "REPORTS (NO DELETIONS MADE)" if dry_run else "CLEANUP COMPLETE"
    print(f"\n--- {status} ---")
    print(f"PDFs kept: {count_kept}")
    print(f"PDFs {'marked for deletion' if dry_run else 'deleted'}: {count_deleted}")

if __name__ == "__main__":
    cleanup_pdfs()
