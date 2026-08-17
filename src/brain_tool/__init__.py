__version__ = "1.2.0"
__author__ = "Vitor Luiz <vitorluizmachado@gmail.com>"

# Re-exporta a API pública para `from brain_tool import ...`.
from .brain_tool import (  # noqa: F401
    SCHEMA_VERSION,
    get_brain_root,
    get_brain_db_path,
    validate_expert_identifier,
    get_database_url,
    get_db_connection,
    get_session,
    initialize_schema,
    list_expert_names,
    generate_canonical_hash,
    remember,
    recall,
    forget,
    synthesize,
    consolidate,
    count_pages,
    learn,
    learn_file,
    learn_directory,
    sync,
    check,
    list_jobs,
    suggest_taxonomy_rules,
    capture_taxonomy,
)

# Submódulos públicos (ORM models + camada de conexão).
from . import db, models  # noqa: F401
