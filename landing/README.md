# TradingAgents – Landingpage

Eine eigenständige, statische Landingpage im Stil von [trading-agents.ai](https://trading-agents.ai/),
die zeigt, wie sich **TradingAgents lokal betreiben** lässt (eigene API-Keys oder
vollständig offline über Ollama).

## Inhalt

- `index.html` – die komplette Seite (HTML + CSS + JS inline, keine externen Abhängigkeiten)
- `assets/` – die Architektur-Grafiken aus dem Projekt

## Lokal ansehen

Die Seite ist rein statisch – einfach `index.html` im Browser öffnen oder einen
kleinen Webserver starten:

```bash
cd landing
python -m http.server 8000
# danach http://localhost:8000 im Browser öffnen
```

## Veröffentlichen (GitHub Pages)

Der Ordner ist self-contained und lässt sich direkt als GitHub-Pages-Quelle nutzen:

- **Settings → Pages → Build and deployment → Source: Deploy from a branch**
- Branch: der Feature-Branch (oder `main` nach dem Merge), Ordner `/landing`

Alternativ funktioniert jeder statische Host (Netlify, Vercel, S3, nginx …),
da die Seite keine externen Requests benötigt (CSP-freundlich).
