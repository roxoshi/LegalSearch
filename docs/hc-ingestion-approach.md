# High Court Data Ingestion: Technical Approach

## Problem

The Supreme Court (SC) data comes as flat zip archives with self-contained metadata — each parquet row has a `case_id`, `citation`, `petitioner`, `respondent`, `year`, and a `path` that directly names the PDF inside a `SC-GST-{year}.zip` file. One zip, one year, simple lookup.

High Court (HC) data is fundamentally different. The metadata lives in parquet files with a different schema, and the PDFs are buried inside a hierarchical tree of tar archives organized by court and bench. The two data sources share almost no column names and their archive structures have nothing in common.

The challenge was: given a metadata record from a parquet file, locate the correct PDF inside the correct tar archive, extract the text, and produce a unified document record that the rest of the pipeline (filtering, embedding, database insert) can process identically to SC data.

## Data Layout

### Metadata (Parquet)

HC parquet files are named `HC-GST-{year}.parquet` and contain ~1.2M records per year. The columns are:

| Column | Example | Notes |
|---|---|---|
| `court_code` | `2~5` | Tilde-separated numeric code |
| `title` | `RFA/924/2012 of NTPC LTD Vs SUNDER` | Case type + parties combined |
| `judge` | `HONOURABLE MR. JUSTICE SANJAY KAROL` | |
| `pdf_link` | `court/cnrorders/cmis/orders/HPHC010168032012_1_2018-03-07.pdf` | Relative path with embedded bench name |
| `cnr` | `HPHC010168032012` | Case Number Record — unique identifier |
| `decision_date` | `2018-03-07` (Timestamp) | |
| `disposal_nature` | `Allowed` / empty | |
| `court` | `High Court of Himachal Pradesh` | |

Notably absent compared to SC: `case_id`, `citation`, `petitioner`, `respondent`, `year`, `path`.

### PDF Archives (Tar)

PDFs are stored in a directory hierarchy:

```
.data/GST_judgments/
  HC-GST-2018/
    court=2_5/
      bench=cmis/
        data.tar          <-- contains thousands of PDFs
        data.index.json
      bench=jammuhc/
        data.tar
    court=27_1/
      bench=kolhcdb/
        data.tar
      bench=newos/
        data.tar
      ...
```

Inside each `data.tar`, filenames have a `./` prefix:

```
./HPHC010168032012_1_2018-03-07.pdf
./HPHC010056582018_1_2018-04-04.pdf
...
```

## The Matching Problem

To go from a parquet row to a PDF, three things must be resolved:

1. **Which year directory?** Derived from `decision_date`.
2. **Which court/bench subdirectory?** Derived from `court_code` and `pdf_link`.
3. **Which file inside the tar?** Derived from the basename of `pdf_link`.

Each of these has a data format mismatch that has to be handled.

### Mismatch 1: court_code encoding

The parquet stores court codes with tildes (`2~5`), but the directory tree uses underscores (`court=2_5`). The resolution is a simple `replace("~", "_")`.

### Mismatch 2: bench name is embedded in pdf_link

The `pdf_link` field has the structure:

```
court/cnrorders/{bench}/orders/{filename}.pdf
```

For example: `court/cnrorders/cmis/orders/HPHC010168032012_1_2018-03-07.pdf`

The bench name (`cmis`) is extracted by splitting on `/` and taking index 2 (after `court` and `cnrorders`). This bench name maps directly to the `bench=cmis` directory.

### Mismatch 3: tar entry prefix

Files inside tar archives have a `./` prefix (`./HPHC010168032012_1_2018-03-07.pdf`) while the `pdf_link` basename does not. The code tries both `./filename` and `filename` when looking up a member.

### Putting it together

Given a parquet record, the tar path is constructed as:

```
{judgments_dir}/HC-GST-{year}/court={court_code}/bench={bench}/data.tar
```

Where:
- `year` = extracted from `decision_date` (the year component)
- `court_code` = parquet `court_code` with `~` replaced by `_`
- `bench` = parsed from `pdf_link` (segment at index 2)

And the filename to find inside the tar is the basename of `pdf_link`.

## Schema Normalization

The downstream pipeline expects a common schema. Since HC parquet columns don't match SC columns, every HC record is normalized:

| Target Field | Source | Derivation |
|---|---|---|
| `case_id` | `cnr` | Used directly — CNR is the unique HC identifier |
| `citation` | `cnr` | Same as case_id (HC has no separate citation system) |
| `title` | `title` | Used directly |
| `petitioner` | `title` | Parsed from "X Vs Y" pattern (see below) |
| `respondent` | `title` | Parsed from "X Vs Y" pattern |
| `judge` | `judge` | Used directly |
| `court` | `court` | Used directly |
| `decision_date` | `decision_date` | Converted from Timestamp to `YYYY-MM-DD` string |
| `year` | `decision_date` | Year component extracted |
| `path` | `pdf_link` | Basename only |
| `disposal_nature` | `disposal_nature` | Used directly |

### Parsing petitioner/respondent from title

HC titles combine the case number with party names in a single string:

```
RFA/924/2012 of NTPC LTD Vs SUNDER
```

The parsing strategy:
1. Find ` of ` and take everything after it (strips the case number prefix)
2. Split on ` Vs ` (also handles `vs`, `VS`, `v/s`, `v.`)
3. Left side = petitioner, right side = respondent
4. Falls back to `("Unknown", "Unknown")` if the pattern doesn't match

## Extraction Pipeline

### Step 1: Load metadata

`load_hc_metadata()` reads all `HC-GST-*.parquet` files from the metadata directory, optionally filtered by year. Each row is normalized to the common schema. Records are deduplicated by CNR (the first occurrence wins).

### Step 2: Parallel extraction from tars

`parallel_extract_from_tars()` takes the normalized records and a base judgments directory. For each record:

1. `_resolve_tar_path()` computes the tar file path + the filename to look for
2. The tar is opened and the PDF is located (trying both `./filename` and `filename`)
3. The PDF bytes are extracted and text is pulled using PyMuPDF
4. A `DocumentData` object is created with all metadata fields plus the extracted text

This runs across multiple processes using `ProcessPoolExecutor`. Each worker independently opens the tar file it needs, which avoids the need for shared file handles across processes.

### Step 3: Downstream (unchanged)

From this point, HC documents are `DocumentData` objects identical in structure to SC documents. The remaining pipeline steps — keyword/NER filtering, PDF-to-HTML conversion, chunking, embedding, database insert — work without any changes.

## Scale

HC data is substantially larger than SC:

- **SC**: ~800 records per year, 9 years = ~7,000 total records
- **HC**: ~1.2M records per year, 9 years = ~10M+ total records

After GST keyword pre-filtering and NER-based relevance filtering (which happen downstream in the pipeline), the actual number of documents ingested is much smaller. The `--years` flag allows processing specific years incrementally rather than loading all 10M+ records at once.

## Integration into the Pipeline

The unified pipeline (`pipelines/unified_pipeline.py`) accepts a `--court-type` argument:

- `sc` — Only loads SC parquets, extracts from zips
- `hc` — Only loads HC parquets, extracts from tars
- `all` — Loads both, extracts from both, concatenates results

The `--years` argument filters HC parquet files by year (e.g., `--years 2018 2019`). This is useful for incremental ingestion given the volume of HC data.
