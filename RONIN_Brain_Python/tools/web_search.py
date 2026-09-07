from __future__ import annotations
from urllib.parse import quote_plus
import requests

def search(query: str, limit: int = 5) -> dict:
    """Search DuckDuckGo's public instant-answer endpoint; returns only actual response data."""
    response = requests.get("https://api.duckduckgo.com/", params={"q": query, "format": "json", "no_html": 1}, timeout=10)
    response.raise_for_status()
    data = response.json()
    results = []
    if data.get("AbstractText"):
        results.append({"title": data.get("Heading") or query, "snippet": data["AbstractText"], "url": data.get("AbstractURL", "")})
    for item in data.get("RelatedTopics", []):
        if isinstance(item, dict) and item.get("Text"):
            results.append({"title": item.get("FirstURL", "").rsplit("/", 1)[-1], "snippet": item["Text"], "url": item.get("FirstURL", "")})
        if len(results) >= limit: break
    return {"query": query, "results": results[:limit], "search_url": f"https://duckduckgo.com/?q={quote_plus(query)}"}
