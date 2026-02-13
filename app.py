"""
Vet Clin Path MCQ Generator
Extracts articles from veterinary clinical pathology journals and generates MCQs
"""

import os
import random
import re
import base64
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, jsonify, request
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key')

PUBMED_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# Journal configurations with PubMed search terms
JOURNALS = {
    "VCP": {
        "name": "Veterinary Clinical Pathology",
        "abbrev": "Vet Clin Pathol",
        "query": '"Vet Clin Pathol"[Journal]',
        "exclude": None
    }
}


def get_date_range(months=12):
    """Get date range for the specified number of months"""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=months * 30)
    return start_date.strftime("%Y/%m/%d"), end_date.strftime("%Y/%m/%d")


def get_article_count(months=12, journal="VCP"):
    """Get total article count from PubMed (no limit) for a specific journal"""
    start_date, end_date = get_date_range(months)

    j_info = JOURNALS.get(journal, JOURNALS["VCP"])
    journal_query = j_info['query']
    if j_info['exclude']:
        journal_query = f"({journal_query} NOT {j_info['exclude']}[Publication Type])"

    query = f'{journal_query} AND ("{start_date}"[Date - Publication] : "{end_date}"[Date - Publication])'

    search_url = f"{PUBMED_BASE_URL}/esearch.fcgi"
    search_params = {
        "db": "pubmed",
        "term": query,
        "retmax": 0,  # Don't need IDs, just the count
        "retmode": "json"
    }

    try:
        response = requests.get(search_url, params=search_params, timeout=30)
        response.raise_for_status()
        search_results = response.json()
        return int(search_results.get("esearchresult", {}).get("count", 0))
    except requests.RequestException as e:
        print(f"Error getting article count: {e}")
        return 0


def search_pubmed_articles(months=12, journal="all"):
    """Search PubMed for articles from specified journal(s)"""
    start_date, end_date = get_date_range(months)

    # Build search query based on journal selection
    if journal == "all":
        # Search all journals
        journal_queries = []
        for j_key, j_info in JOURNALS.items():
            jq = j_info['query']
            if j_info['exclude']:
                jq = f"({jq} NOT {j_info['exclude']}[Publication Type])"
            journal_queries.append(jq)
        journal_query = "(" + " OR ".join(journal_queries) + ")"
    else:
        # Search specific journal
        j_info = JOURNALS.get(journal, JOURNALS["VCP"])
        journal_query = j_info['query']
        if j_info['exclude']:
            journal_query = f"({journal_query} NOT {j_info['exclude']}[Publication Type])"

    query = f'{journal_query} AND ("{start_date}"[Date - Publication] : "{end_date}"[Date - Publication])'

    # First, search for article IDs
    search_url = f"{PUBMED_BASE_URL}/esearch.fcgi"
    search_params = {
        "db": "pubmed",
        "term": query,
        "retmax": 500,
        "retmode": "json",
        "sort": "pub_date"
    }

    try:
        response = requests.get(search_url, params=search_params, timeout=30)
        response.raise_for_status()
        search_results = response.json()

        id_list = search_results.get("esearchresult", {}).get("idlist", [])

        if not id_list:
            return []

        # Fetch article details
        return fetch_article_details(id_list)

    except requests.RequestException as e:
        print(f"Error searching PubMed: {e}")
        return []


def fetch_article_details(pmid_list):
    """Fetch detailed information for articles by PMID"""
    if not pmid_list:
        return []

    fetch_url = f"{PUBMED_BASE_URL}/efetch.fcgi"
    fetch_params = {
        "db": "pubmed",
        "id": ",".join(pmid_list),
        "retmode": "xml",
        "rettype": "abstract"
    }

    try:
        response = requests.get(fetch_url, params=fetch_params, timeout=30)
        response.raise_for_status()

        # Parse XML response
        from xml.etree import ElementTree as ET
        root = ET.fromstring(response.content)

        articles = []
        for article in root.findall(".//PubmedArticle"):
            article_data = parse_article_xml(article)
            if article_data:
                articles.append(article_data)

        return articles

    except requests.RequestException as e:
        print(f"Error fetching article details: {e}")
        return []


def parse_article_xml(article_element):
    """Parse individual article XML element"""
    try:
        medline = article_element.find(".//MedlineCitation")
        if medline is None:
            return None

        pmid_elem = medline.find(".//PMID")
        pmid = pmid_elem.text if pmid_elem is not None else "Unknown"

        article = medline.find(".//Article")
        if article is None:
            return None

        # Title - use itertext() to capture text within nested tags (italics, etc.)
        title_elem = article.find(".//ArticleTitle")
        if title_elem is not None:
            title = "".join(title_elem.itertext())
        else:
            title = "No title"

        # Abstract - use itertext() to capture text within nested tags (italics, etc.)
        abstract_parts = article.findall(".//Abstract/AbstractText")
        if abstract_parts:
            abstract_texts = []
            for part in abstract_parts:
                part_text = "".join(part.itertext())
                if part_text:
                    # Add label if present (for structured abstracts)
                    label = part.get("Label", "")
                    if label:
                        abstract_texts.append(f"{label}: {part_text}")
                    else:
                        abstract_texts.append(part_text)
            abstract = " ".join(abstract_texts) if abstract_texts else "No abstract available"
        else:
            abstract = "No abstract available"

        # Authors
        authors = []
        for author in article.findall(".//AuthorList/Author"):
            last_name = author.find("LastName")
            fore_name = author.find("ForeName")
            if last_name is not None:
                name = last_name.text
                if fore_name is not None:
                    name = f"{fore_name.text} {name}"
                authors.append(name)

        # Publication date
        pub_date = article.find(".//Journal/JournalIssue/PubDate")
        date_str = ""
        if pub_date is not None:
            year = pub_date.find("Year")
            month = pub_date.find("Month")
            if year is not None:
                date_str = year.text
                if month is not None:
                    date_str = f"{month.text} {date_str}"

        # Journal info
        journal = article.find(".//Journal/Title")
        journal_name = journal.text if journal is not None else JOURNAL_NAME

        return {
            "pmid": pmid,
            "title": title,
            "abstract": abstract,
            "authors": ", ".join(authors[:5]) + ("..." if len(authors) > 5 else ""),
            "pub_date": date_str,
            "journal": journal_name,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        }

    except Exception as e:
        print(f"Error parsing article: {e}")
        return None


def generate_mcq_from_article(article, num_questions=1):
    """Generate veterinary clinical pathology MCQ from an article using Claude API"""
    api_key = os.getenv('ANTHROPIC_API_KEY')

    if not api_key:
        return generate_fallback_mcq(article, num_questions, "ANTHROPIC_API_KEY environment variable is not set")

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""Based on the following veterinary clinical pathology article abstract, generate {num_questions} multiple choice question(s) in the style of the ACVP (American College of Veterinary Pathologists) Phase II Certifying Examination.

Article Title: {article['title']}

Abstract: {article['abstract']}

IMPORTANT FORMAT RULES - follow the ACVP Phase II exam style exactly:
- Questions can have 3, 4, or 5 answer choices (vary this naturally)
- Use letter-period format for choices: A. B. C. D. E.
- Questions should be concise and direct
- When possible, present a clinical scenario with species, signalment, and laboratory data
- Include data tables when relevant (format as plain text tables)
- Questions should test knowledge, interpretation, or extended integrated interpretation

Format each question as:
[number]. [Question text - include clinical scenario with species and lab data when relevant]

A. [Option A]
B. [Option B]
C. [Option C]
D. [Option D]

Answer: [Letter]

EXPLANATION: [Detailed explanation of why this is correct and why other options are incorrect, referencing the underlying pathophysiology]

---"""

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        return {
            "article_title": article['title'],
            "article_pmid": article['pmid'],
            "article_url": article['url'],
            "article_authors": article['authors'],
            "article_journal": article['journal'],
            "article_year": article['pub_date'],
            "questions": message.content[0].text
        }

    except Exception as e:
        print(f"Error generating MCQ with API: {e}")
        return generate_fallback_mcq(article, num_questions, str(e))


def generate_fallback_mcq(article, num_questions=1, error_msg=None):
    """Generate a basic template MCQ when API is unavailable"""
    error_note = f"Error: {error_msg}" if error_msg else "Note: API key not configured. Please add your ANTHROPIC_API_KEY to generate AI-powered questions."
    return {
        "article_title": article['title'],
        "article_pmid": article['pmid'],
        "article_url": article['url'],
        "article_authors": article['authors'],
        "article_journal": article['journal'],
        "article_year": article['pub_date'],
        "questions": f"""QUESTION 1:
Based on the study "{article['title']}", which of the following statements is most accurate regarding the findings?

A) [Review the abstract to determine the correct answer]
B) [Alternative interpretation]
C) [Common misconception]
D) [Unrelated finding]
E) [Another distractor]

CORRECT ANSWER: [To be determined after reviewing full article]

EXPLANATION: Please review the full article at {article['url']} to determine the correct answer and explanation.

---
{error_note}"""
    }


# eClinPath topic tree (Cornell University veterinary clinical pathology resource)
ECLINPATH_TOPICS = {
    "Hematology": {
        "url": "https://eclinpath.com/hematology/",
        "subtopics": {
            "Hemogram basics": "https://eclinpath.com/hematology/hemogram-basics/",
            "Blood smear examination": "https://eclinpath.com/hematology/hemogram-basics/blood-smear-examination/",
            "Leukogram patterns": "https://eclinpath.com/hematology/hemogram-basics/leukogram/",
            "RBC morphology": "https://eclinpath.com/hematology/morphologic-features/red-blood-cells/",
            "Normal erythrocytes": "https://eclinpath.com/hematology/morphologic-features/red-blood-cells/normal-erythrocytes/",
            "WBC morphology & leukocytes": "https://eclinpath.com/hematology/leukogram-changes/leukocytes/",
            "Platelet morphology": "https://eclinpath.com/hematology/morphologic-features/platelets/",
            "Reticulocyte count": "https://eclinpath.com/hematology/tests/absolute-reticulocyte-count/",
            "Reticulocyte indices": "https://eclinpath.com/hematology/tests/reticulocyte-indices/",
            "Erythrocytosis / Polycythemia": "https://eclinpath.com/hematology/polycythemia/",
            "WBC counts": "https://eclinpath.com/hematology/tests/wbc-count/",
            "Hematology quick guide": "https://eclinpath.com/hematology/tests/hematology-guide/",
        }
    },
    "Chemistry - Liver": {
        "url": "https://eclinpath.com/chemistry/liver/",
        "subtopics": {
            "ALT (Alanine aminotransferase)": "https://eclinpath.com/chemistry/liver/liver-injury/alanine-aminotransferase/",
            "AST (Aspartate aminotransferase)": "https://eclinpath.com/chemistry/liver/liver-injury/aspartate-aminotransferase/",
            "ALP (Alkaline phosphatase)": "https://eclinpath.com/chemistry/liver/cholestasis/alkaline-phosphatase/",
            "GGT (Gamma-glutamyl transferase)": "https://eclinpath.com/chemistry/liver/cholestasis/gamma-glutamyl-transferase/",
            "Bilirubin": "https://eclinpath.com/chemistry/liver/cholestasis/bilirubin/",
            "Cholestasis": "https://eclinpath.com/chemistry/liver/cholestasis/",
            "Liver function tests": "https://eclinpath.com/chemistry/liver/liver-function-tests/",
            "Laboratory detection of liver disease": "https://eclinpath.com/chemistry/liver/laboratory-detection/",
        }
    },
    "Chemistry - Kidney": {
        "url": "https://eclinpath.com/chemistry/kidney/",
        "subtopics": {
            "Urea nitrogen (BUN)": "https://eclinpath.com/chemistry/kidney/urea-nitrogen/",
            "Creatinine": "https://eclinpath.com/chemistry/kidney/creatinine/",
            "SDMA": "https://eclinpath.com/chemistry/kidney/sdma/",
            "GFR (Glomerular filtration rate)": "https://eclinpath.com/chemistry/kidney/gfr/",
            "Azotemia": "https://eclinpath.com/chemistry/kidney/azotemia/",
            "Types of renal disease": "https://eclinpath.com/chemistry/kidney/types-of-renal-disease/",
            "Renal physiology": "https://eclinpath.com/chemistry/kidney/physiology/",
        }
    },
    "Chemistry - Electrolytes & Acid-Base": {
        "url": "https://eclinpath.com/chemistry/electrolytes/",
        "subtopics": {
            "Electrolytes overview": "https://eclinpath.com/chemistry/electrolytes/",
            "Potassium": "https://eclinpath.com/chemistry/electrolytes/potassium/",
            "Acid-base": "https://eclinpath.com/chemistry/acid-base/",
            "Bicarbonate": "https://eclinpath.com/chemistry/acid-base/chemistry-tests/bicarbonate/",
        }
    },
    "Chemistry - Minerals": {
        "url": "https://eclinpath.com/chemistry/minerals/overview/",
        "subtopics": {
            "Minerals overview (Ca, P, Mg)": "https://eclinpath.com/chemistry/minerals/overview/",
            "Calcium (total)": "https://eclinpath.com/chemistry/minerals/calcium/",
            "Free ionized calcium": "https://eclinpath.com/chemistry/minerals/ionized-calcium/",
            "Phosphate": "https://eclinpath.com/chemistry/minerals/phosphate/",
        }
    },
    "Chemistry - Proteins": {
        "url": "https://eclinpath.com/chemistry/proteins/",
        "subtopics": {
            "Proteins overview": "https://eclinpath.com/chemistry/proteins/",
            "Total protein": "https://eclinpath.com/chemistry/proteins/total-protein/",
        }
    },
    "Chemistry - Energy & Metabolites": {
        "url": "https://eclinpath.com/chemistry/energy-metabolism/",
        "subtopics": {
            "Energy metabolism overview": "https://eclinpath.com/chemistry/energy-metabolism/",
            "Glucose": "https://eclinpath.com/chemistry/energy-metabolism/glucose/",
            "Cholesterol": "https://eclinpath.com/chemistry/energy-metabolism/cholesterol/",
        }
    },
    "Chemistry - Iron Metabolism": {
        "url": "https://eclinpath.com/chemistry/iron-metabolism/",
        "subtopics": {
            "Iron metabolism overview": "https://eclinpath.com/chemistry/iron-metabolism/",
            "Iron physiology": "https://eclinpath.com/chemistry/iron-metabolism/physiology/",
            "Iron distribution": "https://eclinpath.com/chemistry/iron-metabolism/iron-2/",
            "Heme metabolism": "https://eclinpath.com/chemistry/iron-metabolism/heme-metabolism/",
        }
    },
    "Hemostasis": {
        "url": "https://eclinpath.com/hemostasis/",
        "subtopics": {
            "Hemostasis physiology": "https://eclinpath.com/hemostasis/physiology/",
            "Primary hemostasis": "https://eclinpath.com/hemostasis/physiology/primary-hemostasis/",
            "Secondary hemostasis": "https://eclinpath.com/hemostasis/physiology/secondary-hemostasis/",
            "Coagulation cascade": "https://eclinpath.com/hemostasis/physiology/secondary-hemostasis/coagulation-cascade-new-model-3/",
            "Fibrinolysis": "https://eclinpath.com/hemostasis/physiology/fibrinolysis/",
            "Hemostasis tests overview": "https://eclinpath.com/hemostasis/tests/",
            "Screening coagulation assays (PT, APTT)": "https://eclinpath.com/hemostasis/tests/screening-coagulation-assays/",
            "DIC": "https://eclinpath.com/hemostasis/disorders/dic/",
        }
    },
    "Urinalysis": {
        "url": "https://eclinpath.com/urinalysis/",
        "subtopics": {
            "Urinalysis overview": "https://eclinpath.com/urinalysis/",
            "Chemical constituents": "https://eclinpath.com/urinalysis/chemical-constituents/",
            "Cellular constituents": "https://eclinpath.com/urinalysis/cellular-constituents/",
            "Casts": "https://eclinpath.com/urinalysis/casts/",
            "Crystals": "https://eclinpath.com/urinalysis/crystals/",
            "Crystal quick guide": "https://eclinpath.com/urinalysis/crystal-quick-guide/",
            "Cell quick guide": "https://eclinpath.com/urinalysis/cell-quick-quide/",
        }
    },
    "Test Basics": {
        "url": "https://eclinpath.com/test-basics/",
        "subtopics": {
            "Sample collection": "https://eclinpath.com/test-basics/sample-collection-2/",
            "Test interpretation": "https://eclinpath.com/test-basics/test-interpretation/",
            "Interferences": "https://eclinpath.com/test-basics/interferences/",
        }
    },
}


# eClinPath Atlas catalog - image galleries for different pathology specialties
ECLINPATH_ATLAS = {
    "Hematology": {
        "Blood Artifacts": "https://eclinpath.com/atlas/hematology/blood-artifacts/",
        "Blood Smear Features": "https://eclinpath.com/atlas/hematology/blood-smear-features/",
        "RBC Morphology": "https://eclinpath.com/atlas/hematology/erythrocytes/",
        "WBC Features": "https://eclinpath.com/atlas/hematology/leukocytes/",
        "Infectious Agents in Blood": "https://eclinpath.com/atlas/hematology/infectious-agents/",
        "Leukemia": "https://eclinpath.com/atlas/hematology/leukemia/",
        "Canine Blood": "https://eclinpath.com/atlas/hematology/canine-blood/",
        "Feline Blood": "https://eclinpath.com/atlas/hematology/feline-blood/",
        "Equine Blood": "https://eclinpath.com/atlas/hematology/equine-blood/",
        "Ruminant Blood": "https://eclinpath.com/atlas/hematology/ruminant-blood/",
    },
    "Avian & Exotic Hematology": {
        "Avian Blood": "https://eclinpath.com/atlas/hematology/avian-blood/",
        "Reptile Blood": "https://eclinpath.com/atlas/hematology/reptile-blood/",
        "Small Mammal Blood": "https://eclinpath.com/atlas/hematology/small-mammal-blood/",
    },
    "Cytology - Lymph Node": {
        "Lymphoid Cells": "https://eclinpath.com/atlas/cytology/lymph-node/lymphoid-cells/",
        "Lymph Node Neoplasia": "https://eclinpath.com/atlas/cytology/lymph-node/neoplasia/",
        "Lymph Node Reactive": "https://eclinpath.com/atlas/cytology/lymph-node/reactive/",
    },
    "Cytology - Body Fluids": {
        "Peritoneal Fluid": "https://eclinpath.com/atlas/cytology/body-fluids/peritoneal-fluid/",
        "Pleural Fluid": "https://eclinpath.com/atlas/cytology/body-fluids/pleural-fluid/",
        "Synovial Fluid": "https://eclinpath.com/atlas/cytology/body-fluids/synovial-fluid/",
        "Cerebrospinal Fluid": "https://eclinpath.com/atlas/cytology/body-fluids/cerebrospinal-fluid/",
    },
    "Cytology - Organs": {
        "Liver": "https://eclinpath.com/atlas/cytology/organs/liver/",
        "Kidney": "https://eclinpath.com/atlas/cytology/organs/kidney/",
        "Spleen": "https://eclinpath.com/atlas/cytology/organs/spleen/",
        "Bone Marrow": "https://eclinpath.com/atlas/cytology/organs/bone-marrow/",
        "Respiratory Tract": "https://eclinpath.com/atlas/cytology/organs/respiratory-tract/",
    },
    "Cytology - Cutaneous": {
        "Skin Masses": "https://eclinpath.com/atlas/cytology/cutaneous/skin-masses/",
        "Inflammatory Lesions": "https://eclinpath.com/atlas/cytology/cutaneous/inflammatory/",
    },
    "Cytology - Tumors": {
        "Round Cell Tumors": "https://eclinpath.com/atlas/cytology/neoplasia/round-cell-tumors/",
        "Epithelial Tumors": "https://eclinpath.com/atlas/cytology/neoplasia/epithelial-tumors/",
        "Mesenchymal Tumors": "https://eclinpath.com/atlas/cytology/neoplasia/mesenchymal-tumors/",
    },
    "Urinalysis": {
        "Urine Crystals": "https://eclinpath.com/atlas/urinalysis/crystals/",
        "Urine Casts": "https://eclinpath.com/atlas/urinalysis/casts/",
        "Urine Cells": "https://eclinpath.com/atlas/urinalysis/cells/",
        "Urine Artifacts": "https://eclinpath.com/atlas/urinalysis/artifacts/",
        "Urine Infectious Agents": "https://eclinpath.com/atlas/urinalysis/infectious-agents/",
    },
    "Miscellaneous": {
        "Cytochemical Stains": "https://eclinpath.com/atlas/miscellaneous/cytochemical-stains/",
        "Immunocytochemistry": "https://eclinpath.com/atlas/miscellaneous/immunocytochemistry/",
    },
}


def generate_mcq_from_eclinpath(topic_name, subtopic_name, subtopic_url, num_questions=1):
    """Generate MCQ based on eClinPath veterinary clinical pathology topic"""
    api_key = os.getenv('ANTHROPIC_API_KEY')

    if not api_key:
        return {
            "article_title": f"{topic_name} - {subtopic_name}",
            "article_pmid": "",
            "article_url": subtopic_url,
            "article_authors": "eClinPath, Cornell University",
            "article_journal": "eClinPath",
            "article_year": "",
            "questions": "Error: ANTHROPIC_API_KEY environment variable is not set"
        }

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""You are an expert in veterinary clinical pathology. Generate {num_questions} multiple choice question(s) in the style of the ACVP (American College of Veterinary Pathologists) Phase II Certifying Examination.

Topic Category: {topic_name}
Specific Topic: {subtopic_name}
Reference URL: {subtopic_url}

IMPORTANT FORMAT RULES - follow the ACVP Phase II exam style exactly:
- Questions can have 3, 4, or 5 answer choices (vary this naturally based on the question)
- Use letter-period format for choices: A. B. C. D. E.
- Questions should be concise and direct
- Mix question types: some knowledge-only (text), some with clinical scenarios including species, signalment, and laboratory data
- Include data tables when relevant (format as plain text tables with Test, Patient, Flag, Reference Interval columns)
- Include species-specific considerations (dogs, cats, horses, cattle, birds, reptiles, exotics)
- Some questions should present lab data and ask for the most likely diagnosis/condition/interpretation
- Some questions should test specific knowledge (e.g., "In cats, which factor deficiency causes...")

Here are examples of the ACVP exam style:

Example 1 (Knowledge):
1. In cats, prolonged aPTT and normal PT without a bleeding tendency occurs with deficiency of which factor?
A. Factor IX
B. Factor XI
C. Factor VII
D. Factor XII
Answer: D

Example 2 (Interpretation with data table):
2. Laboratory data from an African Grey parrot.
Test (units) | Patient (Baseline) | Flag | Reference Interval | Patient (3h water deprivation) | Patient (post vasopressin)
Sodium (mmol/L) | 159 | H | 134-152 | 159 | -
Urine specific gravity | 1.003 | | 1.005-1.020 | 1.003 | 1.020
Plasma osmolality (mOsmol/kg) | 327 | H | 299-313 | 340 | 312
Which condition is most likely?
A. Diabetes mellitus
B. Medullary washout
C. Psychogenic polydipsia
D. Central diabetes insipidus
Answer: D

Now generate {num_questions} question(s) on the topic "{subtopic_name}" following this exact format:
[number]. [Question text - include clinical scenario with species and lab data when relevant]

A. [Option A]
B. [Option B]
C. [Option C]
D. [Option D]

Answer: [Letter]

EXPLANATION: [Detailed explanation of why this is correct and why other options are incorrect, referencing the underlying pathophysiology]

---"""

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        return {
            "article_title": f"{topic_name} - {subtopic_name}",
            "article_pmid": "",
            "article_url": subtopic_url,
            "article_authors": "eClinPath, Cornell University",
            "article_journal": "eClinPath",
            "article_year": "",
            "questions": message.content[0].text
        }

    except Exception as e:
        print(f"Error generating MCQ from eClinPath topic: {e}")
        return {
            "article_title": f"{topic_name} - {subtopic_name}",
            "article_pmid": "",
            "article_url": subtopic_url,
            "article_authors": "eClinPath, Cornell University",
            "article_journal": "eClinPath",
            "article_year": "",
            "questions": f"Error generating question: {str(e)}"
        }


def fetch_atlas_page(url):
    """Fetch an eClinPath Atlas gallery page and extract image URLs"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://eclinpath.com/',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')

        # Strategy 1: Look for gallery anchor links with data-src or href
        image_urls = []

        # NextGEN Gallery - look for <a> tags with data-src in gallery
        for link in soup.find_all('a', {'data-src': True}):
            img_url = link.get('data-src')
            if img_url and 'eclinpath.com' in img_url and any(ext in img_url.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif']):
                image_urls.append(img_url)

        # Strategy 2: Look for <img> tags with data-src (lazy loading)
        for img in soup.find_all('img', {'data-src': True}):
            img_url = img.get('data-src')
            if img_url and 'eclinpath.com' in img_url and any(ext in img_url.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif']):
                image_urls.append(img_url)

        # Strategy 3: Standard <img> tags with src
        if not image_urls:
            for img in soup.find_all('img', src=True):
                img_url = img.get('src')
                if img_url and 'eclinpath.com' in img_url and any(ext in img_url.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif']):
                    # Exclude small icons and logos
                    if 'icon' not in img_url.lower() and 'logo' not in img_url.lower():
                        image_urls.append(img_url)

        # Strategy 4: Look for srcset attributes (responsive images)
        if not image_urls:
            for img in soup.find_all('img', {'srcset': True}):
                srcset = img.get('srcset')
                # Parse srcset - format is "url1 1x, url2 2x" or "url1 100w, url2 200w"
                urls = re.findall(r'(https?://[^\s,]+)', srcset)
                for img_url in urls:
                    if 'eclinpath.com' in img_url and any(ext in img_url.lower() for ext in ['.jpg', '.jpeg', '.png', '.gif']):
                        image_urls.append(img_url)

        # Remove duplicates while preserving order
        seen = set()
        unique_urls = []
        for url in image_urls:
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)

        print(f"Found {len(unique_urls)} images on {url}")
        return unique_urls

    except Exception as e:
        print(f"Error fetching Atlas page {url}: {e}")
        return []


def fetch_image_as_base64(image_url):
    """Fetch an image and convert it to base64"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
            'Referer': 'https://eclinpath.com/',
        }

        response = requests.get(image_url, headers=headers, timeout=30)
        response.raise_for_status()

        # Detect image type from content-type or URL
        content_type = response.headers.get('Content-Type', '')
        if 'jpeg' in content_type or 'jpg' in image_url.lower():
            media_type = 'image/jpeg'
        elif 'png' in content_type or 'png' in image_url.lower():
            media_type = 'image/png'
        elif 'gif' in content_type or 'gif' in image_url.lower():
            media_type = 'image/gif'
        elif 'webp' in content_type or 'webp' in image_url.lower():
            media_type = 'image/webp'
        else:
            media_type = 'image/jpeg'  # Default fallback

        # Encode to base64
        image_base64 = base64.b64encode(response.content).decode('utf-8')

        return {
            'base64': image_base64,
            'media_type': media_type,
            'url': image_url
        }

    except Exception as e:
        print(f"Error fetching image {image_url}: {e}")
        return None


def generate_mcq_from_atlas(category_name, subcategory_name, gallery_url, num_questions=1):
    """Generate image-based MCQ from eClinPath Atlas gallery"""
    api_key = os.getenv('ANTHROPIC_API_KEY')

    if not api_key:
        return {
            "article_title": f"{category_name} - {subcategory_name}",
            "article_pmid": "",
            "article_url": gallery_url,
            "article_authors": "eClinPath Atlas, Cornell University",
            "article_journal": "eClinPath Atlas",
            "article_year": "",
            "questions": "Error: ANTHROPIC_API_KEY environment variable is not set",
            "has_image": False
        }

    # Fetch images from the gallery
    image_urls = fetch_atlas_page(gallery_url)

    if not image_urls:
        # Fallback: generate text-based question describing what would be seen
        result = generate_atlas_fallback_mcq(category_name, subcategory_name, gallery_url, num_questions)
        result['debug_info'] = f"No images found on page: {gallery_url}"
        return result

    # Select a random image
    selected_image_url = random.choice(image_urls)
    image_data = fetch_image_as_base64(selected_image_url)

    if not image_data:
        # Fallback if image fetch fails
        result = generate_atlas_fallback_mcq(category_name, subcategory_name, gallery_url, num_questions)
        result['debug_info'] = f"Found {len(image_urls)} images but failed to fetch: {selected_image_url}"
        return result

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""You are an expert in veterinary clinical pathology. Analyze this microscopy image from the eClinPath Atlas ({category_name} - {subcategory_name}) and generate {num_questions} multiple choice question(s) in the style of the ACVP Phase II Certifying Examination.

IMPORTANT FORMAT RULES - follow the ACVP Phase II exam style exactly:
- Questions can have 3, 4, or 5 answer choices (vary this naturally based on the question)
- Use letter-period format for choices: A. B. C. D. E.
- Questions should be concise and direct
- Present a clinical scenario with species, signalment, and context for the image
- Ask about interpretation of specific features visible in the image
- The question should reference "the image shown" or "the photomicrograph shown"
- Questions should test interpretation skills appropriate for Section 2 or Section 3 of the ACVP exam

Example format:
1. Blood smear from a 5-year-old Labrador Retriever with lethargy and pale mucous membranes.

What is the morphologic feature indicated in the image?

A. Howell-Jolly bodies
B. Heinz bodies
C. Basophilic stippling
D. Acanthocytes

Answer: A

EXPLANATION: [Detailed explanation of the correct answer and why other options are incorrect]

Now analyze the image and generate {num_questions} question(s):"""

        # Create message with image
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": image_data['media_type'],
                                "data": image_data['base64']
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ]
        )

        return {
            "article_title": f"{category_name} - {subcategory_name}",
            "article_pmid": "",
            "article_url": gallery_url,
            "article_authors": "eClinPath Atlas, Cornell University",
            "article_journal": "eClinPath Atlas",
            "article_year": "",
            "questions": message.content[0].text,
            "has_image": True,
            "image_url": selected_image_url,
            "image_base64": f"data:{image_data['media_type']};base64,{image_data['base64']}",
            "debug_info": f"Successfully fetched image from {len(image_urls)} available images"
        }

    except Exception as e:
        print(f"Error generating MCQ from Atlas with image: {e}")
        return generate_atlas_fallback_mcq(category_name, subcategory_name, gallery_url, num_questions, error=str(e))


def generate_atlas_fallback_mcq(category_name, subcategory_name, gallery_url, num_questions=1, error=None):
    """Generate text-based Atlas MCQ when images can't be fetched"""
    api_key = os.getenv('ANTHROPIC_API_KEY')

    if not api_key:
        error_msg = "ANTHROPIC_API_KEY environment variable is not set"
        return {
            "article_title": f"{category_name} - {subcategory_name}",
            "article_pmid": "",
            "article_url": gallery_url,
            "article_authors": "eClinPath Atlas, Cornell University",
            "article_journal": "eClinPath Atlas",
            "article_year": "",
            "questions": f"Error: {error_msg}",
            "has_image": False
        }

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""You are an expert in veterinary clinical pathology. Generate {num_questions} image-based multiple choice question(s) in the style of the ACVP Phase II Certifying Examination.

Category: {category_name}
Specific Topic: {subcategory_name}
Reference: eClinPath Atlas - {gallery_url}

IMPORTANT FORMAT RULES:
- Questions can have 3, 4, or 5 answer choices (vary this naturally)
- Use letter-period format for choices: A. B. C. D. E.
- Start the question by describing what would be visible in a photomicrograph/image
- For example: "Examine the photomicrograph shown. A blood smear from a 3-year-old horse reveals..."
- Then ask about interpretation of specific features that would be visible
- Questions should test interpretation skills appropriate for Section 2 or Section 3 of the ACVP exam

Example format:
1. Examine the photomicrograph shown. A blood smear from a 5-year-old cat with icterus reveals small, spherical, darkly staining structures within red blood cells.

What are these structures most likely to represent?

A. Howell-Jolly bodies
B. Heinz bodies
C. Basophilic stippling
D. Hemotropic mycoplasma

Answer: B

EXPLANATION: [Detailed explanation]

Generate {num_questions} question(s) about {subcategory_name}:"""

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        questions_text = message.content[0].text
        if error:
            questions_text += f"\n\n(Note: Image could not be loaded due to: {error}. View images at: {gallery_url})"

        return {
            "article_title": f"{category_name} - {subcategory_name}",
            "article_pmid": "",
            "article_url": gallery_url,
            "article_authors": "eClinPath Atlas, Cornell University",
            "article_journal": "eClinPath Atlas",
            "article_year": "",
            "questions": questions_text,
            "has_image": False
        }

    except Exception as e:
        print(f"Error generating fallback Atlas MCQ: {e}")
        return {
            "article_title": f"{category_name} - {subcategory_name}",
            "article_pmid": "",
            "article_url": gallery_url,
            "article_authors": "eClinPath Atlas, Cornell University",
            "article_journal": "eClinPath Atlas",
            "article_year": "",
            "questions": f"Error generating question: {str(e)}",
            "has_image": False
        }


# Routes
@app.route('/')
def index():
    """Main page"""
    return render_template('index.html', journals=JOURNALS)


@app.route('/api/articles')
def get_articles():
    """API endpoint to fetch articles"""
    months = int(request.args.get('months', 12))
    journal = request.args.get('journal', 'all')
    articles = search_pubmed_articles(months, journal)
    return jsonify({
        "success": True,
        "count": len(articles),
        "articles": articles,
        "date_range": get_date_range(months),
        "journal": journal
    })


@app.route('/api/journal-stats')
def get_journal_stats():
    """API endpoint to get article counts by journal for chart (no limit)"""
    months = int(request.args.get('months', 12))

    stats = {}
    for j_key, j_info in JOURNALS.items():
        count = get_article_count(months, j_key)
        stats[j_key] = {
            "name": j_info['name'],
            "abbrev": j_info['abbrev'],
            "count": count
        }

    return jsonify({
        "success": True,
        "stats": stats,
        "months": months
    })


@app.route('/api/eclinpath-topics')
def get_eclinpath_topics():
    """API endpoint to get eClinPath topic tree"""
    return jsonify({
        "success": True,
        "topics": ECLINPATH_TOPICS
    })


@app.route('/api/generate-mcq', methods=['POST'])
def generate_mcq():
    """API endpoint to generate MCQs"""
    data = request.json
    num_questions = int(data.get('num_questions', 5))
    months = int(data.get('months', 12))
    journal = data.get('journal', 'all')

    # Fetch articles using the selected time period and journal
    articles = search_pubmed_articles(months, journal)

    if not articles:
        return jsonify({
            "success": False,
            "error": f"No articles found in the selected time period ({months} months)"
        })

    # Filter articles with abstracts
    articles_with_abstracts = [a for a in articles if a['abstract'] != "No abstract available"]

    if not articles_with_abstracts:
        articles_with_abstracts = articles

    # Randomly select articles for questions
    num_articles = min(num_questions, len(articles_with_abstracts))
    selected_articles = random.sample(articles_with_abstracts, num_articles)

    # Generate MCQs in parallel for faster processing
    mcq_results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(generate_mcq_from_article, article, 1): article for article in selected_articles}
        for future in as_completed(futures):
            try:
                mcq = future.result(timeout=60)
                mcq_results.append(mcq)
            except Exception as e:
                article = futures[future]
                mcq_results.append({
                    "article_title": article['title'],
                    "article_pmid": article['pmid'],
                    "article_url": article['url'],
                    "article_authors": article['authors'],
                    "article_journal": article['journal'],
                    "article_year": article['pub_date'],
                    "questions": f"Error generating question: {str(e)}"
                })

    return jsonify({
        "success": True,
        "mcq_results": mcq_results
    })


@app.route('/api/generate-mcq-eclinpath', methods=['POST'])
def generate_mcq_eclinpath():
    """API endpoint to generate MCQs from eClinPath topics"""
    data = request.json
    num_questions = int(data.get('num_questions', 5))
    selected_topics = data.get('topics', [])

    if not selected_topics:
        return jsonify({
            "success": False,
            "error": "No topics selected"
        })

    # Each item in selected_topics: {"category": "Hematology", "subtopic": "RBC morphology", "url": "..."}
    mcq_results = []
    questions_per_topic = max(1, num_questions // len(selected_topics))
    extra = num_questions - (questions_per_topic * len(selected_topics))

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}
        for i, topic in enumerate(selected_topics):
            q_count = questions_per_topic + (1 if i < extra else 0)
            if q_count <= 0:
                continue
            future = executor.submit(
                generate_mcq_from_eclinpath,
                topic['category'],
                topic['subtopic'],
                topic['url'],
                q_count
            )
            futures[future] = topic

        for future in as_completed(futures):
            try:
                mcq = future.result(timeout=120)
                mcq_results.append(mcq)
            except Exception as e:
                topic = futures[future]
                mcq_results.append({
                    "article_title": f"{topic['category']} - {topic['subtopic']}",
                    "article_pmid": "",
                    "article_url": topic['url'],
                    "article_authors": "eClinPath, Cornell University",
                    "article_journal": "eClinPath",
                    "article_year": "",
                    "questions": f"Error generating question: {str(e)}"
                })

    return jsonify({
        "success": True,
        "mcq_results": mcq_results
    })


@app.route('/api/atlas-categories')
def get_atlas_categories():
    """API endpoint to get eClinPath Atlas categories"""
    return jsonify({
        "success": True,
        "categories": ECLINPATH_ATLAS
    })


@app.route('/api/generate-mcq-atlas', methods=['POST'])
def generate_mcq_atlas():
    """API endpoint to generate image-based MCQs from eClinPath Atlas"""
    data = request.json
    num_questions = int(data.get('num_questions', 5))
    selected_categories = data.get('categories', [])

    if not selected_categories:
        return jsonify({
            "success": False,
            "error": "No categories selected"
        })

    # Each item in selected_categories: {"category": "Hematology", "subcategory": "RBC Morphology", "url": "..."}
    mcq_results = []
    questions_per_category = max(1, num_questions // len(selected_categories))
    extra = num_questions - (questions_per_category * len(selected_categories))

    with ThreadPoolExecutor(max_workers=3) as executor:  # Limit to 3 for image processing
        futures = {}
        for i, cat in enumerate(selected_categories):
            q_count = questions_per_category + (1 if i < extra else 0)
            if q_count <= 0:
                continue
            future = executor.submit(
                generate_mcq_from_atlas,
                cat['category'],
                cat['subcategory'],
                cat['url'],
                q_count
            )
            futures[future] = cat

        for future in as_completed(futures):
            try:
                mcq = future.result(timeout=180)  # 3 minutes timeout for image processing
                mcq_results.append(mcq)
            except Exception as e:
                cat = futures[future]
                mcq_results.append({
                    "article_title": f"{cat['category']} - {cat['subcategory']}",
                    "article_pmid": "",
                    "article_url": cat['url'],
                    "article_authors": "eClinPath Atlas, Cornell University",
                    "article_journal": "eClinPath Atlas",
                    "article_year": "",
                    "questions": f"Error generating question: {str(e)}",
                    "has_image": False
                })

    return jsonify({
        "success": True,
        "mcq_results": mcq_results
    })


if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(debug=debug_mode, host='0.0.0.0', port=5000)
