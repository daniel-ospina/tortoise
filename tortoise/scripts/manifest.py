# #7049 Manifest — registered sync scripts for the org-sync mini-epic.
#
# Each entry maps a script to its issue, entity type, and graph target.
# Consumed by sync_schedule.py and sync_verify.py for reporting.
#
# ponytail: YAML is stdlib-free via PyYAML if available, but this is read
# manually by scripts that import it. Keep it importable as plain Python dict.
sync_manifest = {
    "scripts": [
        {
            "issue": 7042,
            "script": "sync_github.py",
            "entity": "Subject+Object",
            "source": "GitHub API (gh orgs/<org>/teams, repos)",
            "graph_node": "Subject(team), Object(repository)",
        },
        {
            "issue": 7043,
            "script": "sync_products.py",
            "entity": "Object",
            "source": "docs/teams/*/product/*.md",
            "graph_node": "Object(product)",
        },
        {
            "issue": 7044,
            "script": "sync_roles.py",
            "entity": "Subject",
            "source": "operations/subjects/*.md",
            "graph_node": "Subject(role)",
        },
        {
            "issue": 7045,
            "script": "sdk.backfill_about_entities()",
            "entity": "Point.aboutEntities",
            "source": "Keyword match Point.content ↔ Subject/Object.name",
            "graph_node": "Point property update",
        },
        {
            "issue": 7046,
            "script": "sync_features.py",
            "entity": "Object",
            "source": "GitHub API (issues labeled feature/enhancement)",
            "graph_node": "Object(feature)",
        },
        {
            "issue": 7047,
            "script": "sync_schedule.py",
            "entity": "Orchestrator",
            "source": "Runs all sync scripts sequentially",
            "graph_node": "N/A (runner)",
        },
        {
            "issue": 7048,
            "script": "sync_verify.py",
            "entity": "Verification",
            "source": "Counts graph entities vs expected kinds",
            "graph_node": "N/A (reporter)",
        },
        {
            "issue": 7049,
            "script": "manifest.py",
            "entity": "Manifest",
            "source": "This file — registry of all sync scripts",
            "graph_node": "N/A (metadata)",
        },
    ]
}
