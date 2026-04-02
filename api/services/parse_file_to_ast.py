import logging
from pathlib import Path

from api.models.ast_results import CallSite, ClassDef, FunctionDef, Import, ParsedFile
from common.languages import EXT_TO_LANG
from common.tree_sitter_manager import _get_lang_parser

logger = logging.getLogger(__name__)


def _ast_parse_file_full(file_path: str, source: str) -> ParsedFile:
    """Parse one file with full AST extraction including source and docstring."""
    ext = Path(file_path).suffix.lower()
    lang = EXT_TO_LANG.get(ext)
    if not lang:
        return ParsedFile(
            path=file_path,
            language="unknown",
            functions=[],
            classes=[],
            imports=[],
            call_sites=[],
            error=f"Unsupported extension: {ext}",
        )

    parser = _get_lang_parser(lang, index_source=True)
    if not parser:
        return ParsedFile(
            path=file_path,
            language=lang,
            functions=[],
            classes=[],
            imports=[],
            call_sites=[],
            error=f"No parser for {lang}",
        )

    try:
        tree = parser.parser.parse(source.encode("utf-8"))
        root = tree.root_node

        # Extract full data using parser methods (with index_source=True)
        raw_imports = parser._find_imports(root) if hasattr(parser, "_find_imports") else []
        raw_functions = (
            parser._find_functions(root, index_source=True)
            if hasattr(parser, "_find_functions")
            else []
        )
        raw_classes = (
            parser._find_classes(root, index_source=True)
            if hasattr(parser, "_find_classes")
            else []
        )
        raw_calls = parser._find_calls(root) if hasattr(parser, "_find_calls") else []

        # Convert to structured data
        functions = []
        for f in raw_functions:
            func = FunctionDef(
                name=f.get("name", ""),
                line_number=f.get("line_number", 0),
                end_line=f.get("end_line", f.get("line_number", 0)),
                args=f.get("args", []),
                cyclomatic_complexity=f.get("cyclomatic_complexity", 1),
                context=f.get("context") or "",
                context_type=f.get("context_type") or "",
                class_context=f.get("class_context") or "",
                decorators=f.get("decorators", []),
                docstring=f.get("docstring"),
                source=f.get("source", ""),
                is_method=bool(f.get("class_context")),
            )
            functions.append(func)

        classes = []
        for c in raw_classes:
            cls = ClassDef(
                name=c.get("name", ""),
                line_number=c.get("line_number", 0),
                end_line=c.get("end_line", c.get("line_number", 0)),
                bases=c.get("bases", []),
                context=c.get("context") or "",
                decorators=c.get("decorators", []),
                docstring=c.get("docstring"),
                source=c.get("source", ""),
            )
            classes.append(cls)

        imports = []
        for imp in raw_imports:
            imports.append(
                Import(
                    name=imp.get("name", ""),
                    full_import_name=imp.get("full_import_name") or imp.get("source", ""),
                    line_number=imp.get("line_number", 0),
                    alias=imp.get("alias"),
                )
            )

        call_sites = []
        for call in raw_calls:
            context_info = call.get("context") or (None, None, None)
            if not isinstance(context_info, (list, tuple)):
                context_info = (None, None, None)
            call_sites.append(
                CallSite(
                    name=call.get("name", ""),
                    full_name=call.get("full_name", call.get("name", "")),
                    line_number=call.get("line_number", 0),
                    args=call.get("args", []),
                    context=context_info[0] if context_info[0] else "",
                    context_type=context_info[1]
                    if len(context_info) > 1 and context_info[1]
                    else "",
                    class_context=str(call.get("class_context"))
                    if call.get("class_context")
                    else "",
                )
            )

        return ParsedFile(
            path=file_path,
            language=lang,
            functions=functions,
            classes=classes,
            imports=imports,
            call_sites=call_sites,
        )
    except Exception as e:
        logger.warning("AST parse failed for %s: %s", file_path, e)
        return ParsedFile(
            path=file_path,
            language=lang,
            functions=[],
            classes=[],
            imports=[],
            call_sites=[],
            error=str(e),
        )
