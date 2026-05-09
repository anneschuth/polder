from bs4 import BeautifulSoup

with open("_cache/abd-nieuws/suzie-kewal-afdelingshoofd-met-aandachtsgebieden-ai-algoritmen-data-en-digitale-inclusie-bij-bzk-2023-11-27.html", encoding="utf-8") as f:
    html = f.read()

soup = BeautifulSoup(html, "html.parser")

canonical = soup.find("link", rel="canonical")
print("CANONICAL:", canonical.get("href") if canonical else "none")

meta_id = soup.find("meta", attrs={"name": "DCTERMS.identifier"})
print("DCTERMS.identifier:", meta_id.get("content") if meta_id else "none")

article = soup.find("article") or soup.find("main")
if article:
    text = article.get_text(separator=" ", strip=True)
    print("ARTICLE LEN:", len(text))
    print("=== FIRST 5000 ===")
    print(text[:5000])
else:
    print("No article/main found, trying body")
    body = soup.find("body")
    text = body.get_text(separator=" ", strip=True) if body else html
    print(text[:5000])
