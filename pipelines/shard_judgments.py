import pandas as pd
import json
import os
import zipfile
import io
import pdfplumber
from pathlib import Path
from tqdm import tqdm

METADATA_RAW_DIR = ".data/metadata/raw"
GST_JUDGMENTS_DIR = ".data/GST_judgments"
METADATA_PROCESSED_DIR = ".data/metadata/processed"

KEEP_COLUMNS = [
    'title', 'petitioner', 'respondent', 'judge', 
    'citation', 'case_id', 'cnr', 'decision_date', 
    'disposal_nature', 'court', 'nc_display', 'year', 'path'
]

class FullTextExtractor:
    @staticmethod
    def extract_from_zip(zip_path, record_path):
        if not os.path.exists(zip_path):
            return ""
        try:
            with zipfile.ZipFile(zip_path, 'r') as z:
                all_files = z.namelist()
                # Get the filename from the path (e.g. 2023_101.pdf)
                filename = os.path.basename(record_path)
                
                # Zip content search: case-insensitive match
                actual_target = next((f for f in all_files if f.lower().endswith(filename.lower())), None)
                
                # If direct match fails, try looking for the _EN version common in the dataset
                if not actual_target:
                    base = filename.rsplit('.', 1)[0]
                    en_filename = f"{base}_EN.pdf"
                    actual_target = next((f for f in all_files if f.lower().endswith(en_filename.lower())), None)

                if not actual_target:
                    return ""

                with z.open(actual_target) as f:
                    with pdfplumber.open(io.BytesIO(f.read())) as pdf:
                        full_content = []
                        for page in pdf.pages:
                            text = page.extract_text()
                            if text:
                                full_content.append(text)
                                if len(full_content) > 6000:
                                    full_content = full_content[:6000]
                                    break
                        return "\n\n".join(full_content).strip()
        except Exception:
            return ""

def process_and_shard_data():
    files = [f for f in os.listdir(METADATA_RAW_DIR) if f.endswith('.parquet')]
    print("📋 Loading metadata records...")
    temp_records = []
    for file in files:
        df = pd.read_parquet(os.path.join(METADATA_RAW_DIR, file), columns=KEEP_COLUMNS)
        temp_records.extend(df.to_dict(orient='records'))

    print(f"🚀 Processing {len(temp_records)} judgments into individual JSON files...")
    
    success_count = 0
    seen_citations = set()

    for record in tqdm(temp_records, desc="Extracting & Saving", unit="file"):
        cite = record.get('citation')
        year = str(record.get('year'))
        
        if not cite or cite in seen_citations:
            continue
        
        zip_name = f"SC-GST-{year}.zip"
        zip_path = os.path.join(GST_JUDGMENTS_DIR, zip_name)
        record['full_text'] = FullTextExtractor.extract_from_zip(zip_path, record['path'])
        
        # 1. Create Directory Structure
        year_dir = Path(METADATA_PROCESSED_DIR) / f"SC_GST_{year}"
        year_dir.mkdir(parents=True, exist_ok=True)
        
        # 2. FIXED FILENAME LOGIC: No _EN, just .json
        # Path(record['path']).stem gets the name without .pdf / .PDF
        json_filename = f"{Path(record['path']).stem}.json"
        output_path = year_dir / json_filename

        # 3. Save individual JSON
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(record, f, indent=4, ensure_ascii=False)
        
        seen_citations.add(cite)
        success_count += 1

    print(f"\n✨ DONE: Saved {success_count} files as .json in {METADATA_PROCESSED_DIR}")

if __name__ == "__main__":
    process_and_shard_data()