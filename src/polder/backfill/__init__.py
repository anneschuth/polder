"""Backfill-implementaties voor historische data.

Doel: een eerdere snapshot van een bron (cached HTML / XML) opnieuw door de
huidige skill halen, zodat schema-tweaks en skill-versies hun beslag krijgen
op historische data zonder de volle download-kosten opnieuw te maken.

Werkt via dezelfde `polder.llm.runner.run_skill` als de daily ingest, dus
prompt-caching en response-cache stapelen automatisch.
"""
