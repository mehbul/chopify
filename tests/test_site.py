"""Validate the deployable bundle and guard against publishing local data."""
from html.parser import HTMLParser
import json
import xml.etree.ElementTree as ET

import pytest
from tools.build_site import build


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.jsonld = []
        self.in_schema = False
        self.canonical = None
        self.assets = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self.in_schema = True
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonical = attrs["href"]
        if tag == "img":
            self.assets.append(attrs["src"])
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.assets.append(attrs["href"])

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_schema = False

    def handle_data(self, data):
        if self.in_schema:
            self.jsonld.append(data)


def test_public_bundle_has_consistent_metadata_and_working_assets(tmp_path):
    out = build(tmp_path / "site")
    page = Page()
    page.feed((out / "index.html").read_text(encoding="utf-8"))
    graph = json.loads("".join(page.jsonld))["@graph"]
    app = next(x for x in graph if "SoftwareApplication" in x["@type"])
    product = json.loads((out / "product.json").read_text())
    assert app["name"] == product["name"]
    assert app["url"] == product["website"] == page.canonical
    assert app["codeRepository"] == product["repository"]
    assert float(app["offers"]["price"]) == product["price"]["amount"]
    loc = ET.parse(out / "sitemap.xml").find(".//{*}loc").text
    assert loc == page.canonical
    assert all((out / asset).is_file() for asset in page.assets)
    assert "## Local HTTP API" in (out / "llms-full.txt").read_text(encoding="utf-8")
    assert not (out / "projects").exists()


def test_build_refuses_to_publish_unrelated_output_files(tmp_path):
    out = build(tmp_path / "site")
    (out / "private-transcript.json").write_text('{"secret":"private"}')
    with pytest.raises(ValueError, match="unrelated"):
        build(out)
