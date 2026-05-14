#!/usr/bin/env python3
"""
Zenodo upload script for TIC/CIT Papers 6-9.
Usage: ZENODO_TOKEN=<your_token> python3 zenodo_upload.py

After uploading paper 6, update zenodo_metadata.json with the DOI,
then run again for paper 7, and so on (6 -> 7 -> 8 -> 9 in order).
"""

import json
import os
import sys
import requests
from pathlib import Path

ZENODO_API = "https://zenodo.org/api"
TOKEN = os.environ.get("ZENODO_TOKEN")
if not TOKEN:
    print("Error: set ZENODO_TOKEN environment variable")
    sys.exit(1)

HEADERS = {"Authorization": f"Bearer {TOKEN}"}
SCRIPT_DIR = Path(__file__).parent

def create_deposit(metadata: dict) -> dict:
    r = requests.post(f"{ZENODO_API}/deposit/depositions",
                      headers=HEADERS,
                      json={"metadata": metadata})
    r.raise_for_status()
    return r.json()

def upload_file(deposit_id: int, filepath: Path) -> None:
    bucket_url = requests.get(
        f"{ZENODO_API}/deposit/depositions/{deposit_id}",
        headers=HEADERS
    ).json()["links"]["bucket"]

    with open(filepath, "rb") as f:
        r = requests.put(f"{bucket_url}/{filepath.name}",
                         headers=HEADERS,
                         data=f)
    r.raise_for_status()

def publish(deposit_id: int) -> dict:
    r = requests.post(f"{ZENODO_API}/deposit/depositions/{deposit_id}/actions/publish",
                      headers=HEADERS)
    r.raise_for_status()
    return r.json()

def build_zenodo_metadata(paper: dict) -> dict:
    related = []
    for ri in paper["related_identifiers"]:
        if "PAPER_" in ri["identifier"]:
            print(f"  Warning: skipping unresolved identifier {ri['identifier']}")
            continue
        related.append({
            "identifier": ri["identifier"],
            "relation": ri["relation"],
            "scheme": ri["scheme"]
        })

    return {
        "title": paper["title"],
        "upload_type": paper["upload_type"],
        "publication_type": paper["publication_type"],
        "description": paper["description"],
        "creators": [
            {
                "name": a["name"],
                "orcid": a.get("orcid"),
                "affiliation": a.get("affiliation")
            }
            for a in paper["authors"]
        ],
        "keywords": paper["keywords"],
        "related_identifiers": related,
        "license": paper["license"],
        "access_right": "open"
    }

def main():
    with open(SCRIPT_DIR / "zenodo_metadata.json") as f:
        papers = json.load(f)

    for paper in papers:
        if paper["zenodo_doi"]:
            print(f"Paper {paper['paper']} already uploaded: {paper['zenodo_doi']}")
            continue

        print(f"\nUploading Paper {paper['paper']}: {paper['title'][:60]}...")

        zenodo_meta = build_zenodo_metadata(paper)
        deposit = create_deposit(zenodo_meta)
        deposit_id = deposit["id"]
        print(f"  Created deposit ID: {deposit_id}")

        pdf_path = SCRIPT_DIR / paper["file"]
        upload_file(deposit_id, pdf_path)
        print(f"  Uploaded: {paper['file']}")

        result = publish(deposit_id)
        doi = result["doi"]
        print(f"  Published! DOI: {doi}")

        # Update metadata file with new DOI
        paper["zenodo_doi"] = doi
        next_paper_num = paper["paper"] + 1
        for p in papers:
            for ri in p["related_identifiers"]:
                if ri["identifier"] == f"PAPER_{paper['paper']}_DOI":
                    ri["identifier"] = doi
                    print(f"  Updated Paper {p['paper']} 'Continues' -> {doi}")

        with open(SCRIPT_DIR / "zenodo_metadata.json", "w") as f:
            json.dump(papers, f, indent=2, ensure_ascii=False)

        print(f"  Metadata saved.")
        break  # upload one at a time so DOIs can propagate

    print("\nDone. Run again to upload the next paper.")

if __name__ == "__main__":
    main()
