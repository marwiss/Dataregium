# This file was generated with the assistance of an AI coding tool.
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from opencde.settings import GraphDialect, Settings, Tier


def _unset(**values: object) -> Settings:
    """Settings as the environment gives them, the tier too."""
    values.setdefault("data_dir", Path(tempfile.mkdtemp()) / "data")  # a generated key lands there, not in the tree
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def _settings(**values: object) -> Settings:
    """Settings of a server of tier 3 unless the values name a tier, as most of these keep the data in a graph."""
    values.setdefault("tier", "experimental")
    return _unset(**values)


PROD = {"public_url": "https://cde.example.com", "graph_password": "secret-enough", "secret_key": "k" * 40}


def test_production_requires_https_public_url() -> None:
    with pytest.raises(ValidationError, match="https"):
        _settings(environment="production", **{**PROD, "public_url": "http://cde.example.com"})


def test_production_requires_graph_password_and_secret_key() -> None:
    with pytest.raises(ValidationError, match="OPENCDE_GRAPH_PASSWORD"):
        _settings(environment="production", **{**PROD, "graph_password": ""})
    with pytest.raises(ValidationError, match="OPENCDE_SECRET_KEY"):
        _settings(environment="production", **{**PROD, "secret_key": "short"})
    with pytest.raises(ValidationError, match="LOCALHOST"):
        _settings(environment="production", **PROD, allow_localhost_redirects=True)
    assert _settings(environment="production", **PROD).is_production


def test_a_tier_holds_back_what_is_above_it_and_tier_1_offers_the_documents_api() -> None:
    """OB-46 b: a function can be off within the tier, never on above it, and tiers 1 and 2 keep the server's own
    data in PostgreSQL alone (F58 a); unset, the tier is bim-gis (F75 b)."""
    postgres = {"environment": "development", "store_url": "postgresql://cde@db/cde"}
    assert _unset(**postgres).tier is Tier.BIM_GIS, "as opencde init writes by default"
    documents = _settings(**postgres, tier="documents")
    assert documents.apis == ["documents"] and documents.offers("foundation") and not documents.offers("bcf-3.0")
    assert _settings(**postgres, tier="bim-gis").apis == ["bcf-3.0", "bcf-2.1", "documents"]
    for values, words in (
        ({"tier": "documents", "apis": ["documents", "bcf-2.1"]}, "OPENCDE_APIS names bcf-2.1"),
        ({"tier": "documents", "mcp_enabled": True}, "OPENCDE_MCP_ENABLED is tier 2"),
        ({"tier": "documents", "bim_enabled": True}, "OPENCDE_BIM_ENABLED is tier 2"),
        ({"tier": "bim-gis", "ifcgraph_enabled": True}, "OPENCDE_IFC_DB_ENABLED is tier 3"),
    ):
        with pytest.raises(ValidationError, match=words):
            _settings(**postgres, **values)
    with pytest.raises(ValidationError, match="PostgreSQL alone"):
        _settings(environment="development", tier="bim-gis")
    assert _settings(environment="development", tier="experimental", ifcgraph_enabled=True).uses_graph
    bim = _settings(**postgres, tier="bim-gis", bim_enabled=True)
    assert bim.bim_on and not bim.uses_graph, "the BIM functions of tier 2 on the files, without a graph (F59)"
    assert _settings(environment="development", ifcgraph_enabled=True).bim_on, "IFC-DB brings them along"


def test_a_server_that_names_no_tier_is_of_tier_2_and_is_told_how_to_keep_the_graph() -> None:
    """F75 b: a server from before the tiers, with IFC-DB or with its own data in a graph, does not start until
    OPENCDE_TIER=experimental is set, and the refusal says so."""
    for values, words in (
        ({"environment": "development"}, "or OPENCDE_TIER=experimental to keep it in the graph"),
        ({"environment": "development", "store_url": "postgresql://cde@db/cde", "ifcgraph_enabled": True}, "tier 3"),
    ):
        with pytest.raises(ValidationError, match="the default when it is unset") as refused:
            _unset(**values)
        assert words in str(refused.value)
    assert _unset(environment="development", tier="experimental").tier is Tier.EXPERIMENTAL


def test_production_with_postgresql_needs_its_password_and_a_graph_password_only_with_a_graph() -> None:
    """Tiers 1 and 2 keep the server's own data in PostgreSQL and use no graph (F58 a): production needs
    OPENCDE_STORE_PASSWORD then, and OPENCDE_GRAPH_PASSWORD only when IFC-DB uses the graph."""
    postgres = {**PROD, "graph_password": "", "store_url": "postgresql://cde@db:5432/cde"}
    with pytest.raises(ValidationError, match="OPENCDE_STORE_PASSWORD is required in production"):
        _settings(environment="production", **postgres)
    settings = _settings(environment="production", **postgres, store_password="s" * 20)
    assert settings.is_production and not settings.uses_graph
    with pytest.raises(ValidationError, match="OPENCDE_GRAPH_PASSWORD is required in production"):
        _settings(environment="production", **postgres, store_password="s" * 20, ifcgraph_enabled=True)
    local = {**LOCAL, "graph_password": "", "store_url": "postgresql://cde@db:5432/cde"}
    with pytest.raises(ValidationError, match="OPENCDE_STORE_PASSWORD is required in the local environment"):
        _settings(**local, public_url="http://127.0.0.1:8000")
    assert _settings(**local, public_url="http://127.0.0.1:8000", store_password="s" * 20).is_local


def test_a_published_bolt_address_must_be_encrypted_in_production() -> None:
    """An open database port carries the database password; production announces it only over TLS."""
    for insecure in ("bolt://cde.example.com:7687", "neo4j+ssc://cde.example.com:7687"):
        with pytest.raises(ValidationError, match="bolt\\+s://"):
            _settings(environment="production", **PROD, graph_public_bolt_url=insecure)
    secure = _settings(environment="production", **PROD, graph_public_bolt_url="bolt+s://cde.example.com:7687")
    assert secure.graph_public_bolt_url == "bolt+s://cde.example.com:7687"
    with pytest.raises(ValidationError, match="OPENCDE_GRAPH_PUBLIC_BOLT_URL"):
        _settings(environment="development", graph_public_bolt_url="https://cde.example.com:7687")
    local = _settings(environment="development", graph_public_bolt_url="bolt://localhost:7697")
    assert local.graph_public_bolt_url == "bolt://localhost:7697", "plain Bolt is fine outside production"
    blank = _settings(environment="development", graph_public_bolt_url="  ")
    assert blank.graph_public_bolt_url is None, "blank means not published"


def test_development_allows_http_localhost_and_generates_a_key(tmp_path: Path) -> None:
    settings = _settings(environment="development", public_url="http://localhost:8000/", data_dir=tmp_path / "data")
    assert settings.public_url == "http://localhost:8000"  # trailing slash removed for safe concatenation
    assert len(settings.secret_key.get_secret_value()) >= 32
    assert settings.url("/foundation/versions") == "http://localhost:8000/foundation/versions"


def test_a_generated_key_is_kept_in_the_data_directory(tmp_path: Path) -> None:
    """Restarts and worker processes sign forms and sessions with the same key; an explicit key is left alone."""
    first = _settings(environment="development", data_dir=tmp_path / "data")
    second = _settings(environment="development", data_dir=tmp_path / "data")
    assert first.secret_key.get_secret_value() == second.secret_key.get_secret_value()
    assert (tmp_path / "data" / "secret_key").read_text(encoding="utf-8") == first.secret_key.get_secret_value()
    explicit = _settings(environment="development", data_dir=tmp_path / "data", secret_key="k" * 40)
    assert explicit.secret_key.get_secret_value() == "k" * 40
    elsewhere = _settings(environment="development", data_dir=tmp_path / "other")
    assert elsewhere.secret_key.get_secret_value() != first.secret_key.get_secret_value()


LOCAL = {"environment": "local", "graph_password": "secret-enough", "secret_key": "k" * 40, "cors_origins": ["null"]}


def test_the_local_engine_keeps_to_the_loopback_and_names_its_origins() -> None:
    local = _settings(**LOCAL, public_url="http://127.0.0.1:8000")
    assert local.is_local and not local.is_production
    assert _settings(**LOCAL, public_url="http://localhost:8000").is_local
    with pytest.raises(ValidationError, match="loopback"):
        _settings(**LOCAL, public_url="http://engine.example")
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        _settings(**{**LOCAL, "cors_origins": ["*"]}, public_url="http://localhost:8000")
    with pytest.raises(ValidationError, match="GRAPH_PASSWORD"):
        _settings(**{**LOCAL, "graph_password": ""}, public_url="http://127.0.0.1:8000")
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        _settings(**{**LOCAL, "secret_key": "short"}, public_url="http://127.0.0.1:8000")
    with pytest.raises(ValidationError, match="BOLT_URL"):
        _settings(**LOCAL, public_url="http://127.0.0.1:8000", graph_public_bolt_url="bolt://localhost:7697")


def test_graph_uri_scheme_is_checked() -> None:
    with pytest.raises(ValidationError, match="OPENCDE_GRAPH_URI"):
        _settings(environment="development", graph_uri="http://localhost:7687")


def test_database_name_follows_dialect() -> None:
    assert _settings(environment="development").database_name == "neo4j"
    assert _settings(environment="development", graph_dialect=GraphDialect.MEMGRAPH).database_name == "memgraph"
    assert _settings(environment="development", graph_database="dataregium").database_name == "dataregium"


def test_upload_part_cannot_exceed_upload_limit() -> None:
    with pytest.raises(ValidationError, match="UPLOAD_PART"):
        _settings(environment="development", max_upload_bytes=10, upload_part_bytes=20)
