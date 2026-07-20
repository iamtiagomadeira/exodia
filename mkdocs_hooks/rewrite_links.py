"""MkDocs hook: rewrite GitHub-relative links to their in-site page paths.

The Markdown files at the repo root (README, CONTRIBUTING, ...) link to each
other with GitHub-relative paths like ``CONTRIBUTING.md`` or ``LICENSE`` so they
render correctly on GitHub. When those same files are included into the docs
site via pymdownx snippets, those links would 404.

Snippets are expanded *during* Markdown conversion — after ``on_page_markdown``
runs — so we rewrite on the rendered HTML in ``on_post_page`` instead, where the
included content (and its links) is present.
"""

from __future__ import annotations

# Map a GitHub-relative href -> in-site relative href (from a top-level page).
_HREF_MAP = {
    "CONTRIBUTING.md": "contributing/",
    "SECURITY.md": "security/",
    "CHANGELOG.md": "changelog/",
    "docs/authoring-a-module.md": "authoring-a-module/",
    "authoring-a-module.md": "authoring-a-module/",
    "LICENSE": "https://github.com/iamtiagomadeira/sap-migration-toolkit/blob/main/LICENSE",
}


def on_post_page(output: str, **_kwargs: object) -> str:
    """Rewrite known GitHub-relative hrefs in the rendered HTML."""
    for src, dst in _HREF_MAP.items():
        output = output.replace(f'href="{src}"', f'href="{dst}"')
    return output
