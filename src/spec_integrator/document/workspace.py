"""Document loading and DocGraph construction use case."""

from dataclasses import dataclass
from pathlib import Path

from spec_integrator.config import Config
from spec_integrator.db import DocAuditDB
from spec_integrator.graph import DocGraphBuilder, Graph
from spec_integrator.models import ParsedDocument
from spec_integrator.parser import MarkdownParser


@dataclass
class DocumentWorkspace:
    documents: list[ParsedDocument]
    graph: Graph
    db: DocAuditDB
    docs_root: Path


def load_workspace(
    config: Config,
    clean: bool = False,
    file_paths: list[str | Path] | None = None,
) -> DocumentWorkspace:
    """Parse the selected documents and build their graph and persistence state."""
    docs_root = config.get_docs_dir()
    if not docs_root.exists():
        raise FileNotFoundError(f"Docs directory not found: {docs_root}")

    db = DocAuditDB(config.get_db_path())
    if clean:
        db.clear_all()

    parser = MarkdownParser(config)
    if file_paths:
        md_files = [
            Path(file_path).resolve()
            for file_path in file_paths
            if Path(file_path).is_file() and not config.is_excluded(file_path, docs_root)
        ]
    else:
        md_files = [
            file_path
            for file_path in sorted(docs_root.rglob("*.md"))
            if not config.is_excluded(file_path, docs_root)
        ]

    documents: list[ParsedDocument] = []
    for md_file in md_files:
        document = parser.parse_file(md_file, docs_root)
        documents.append(document)
        db.insert_document(
            document.file_path,
            document.tier,
            document.component,
            document.content_hash,
        )
        for section in document.sections:
            section_hash = parser.config_compute_hash(section.body_text)
            db.insert_section(
                section.section_id,
                document.file_path,
                section.heading,
                section.level,
                section.line_start,
                section.line_end,
                section.body_text,
                section_hash,
            )
            for keyword in section.keywords:
                relation = (
                    "defines"
                    if config.is_keyword_definition(keyword, document.file_path)
                    else "refers_to"
                )
                db.insert_keyword_reference(
                    keyword,
                    document.file_path,
                    section.section_id,
                    relation,
                    section.line_start,
                )
        for link in document.all_links:
            db.insert_link(
                link.source_file,
                link.source_line,
                link.target_path,
                link.target_anchor,
                1,
            )

    db.commit()
    graph = DocGraphBuilder(config).build(documents, docs_root)
    return DocumentWorkspace(documents, graph, db, docs_root)
