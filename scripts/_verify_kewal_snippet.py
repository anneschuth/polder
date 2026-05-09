from bs4 import BeautifulSoup

with open("_cache/abd-nieuws/suzie-kewal-afdelingshoofd-met-aandachtsgebieden-ai-algoritmen-data-en-digitale-inclusie-bij-bzk-2023-11-27.html", encoding="utf-8") as f:
    html = f.read()

soup = BeautifulSoup(html, "html.parser")
article = soup.find("article") or soup.find("main")
raw_text = article.get_text(separator=" ", strip=True)

snippet = "Suzie Kewal start op 1 januari 2024 als afdelingshoofd met aandachtsgebieden AI, Algoritmen, Data en Digitale Inclusie bij de directie Digitale Samenleving van het ministerie van Binnenlandse Zaken en Koninkrijksrelaties."

assert snippet in raw_text, f"FAIL: snippet niet gevonden in article text"
print("PASS: evidence_snippet is letterlijke substring van artikel-tekst")
print(f"Snippet: {snippet!r}")
