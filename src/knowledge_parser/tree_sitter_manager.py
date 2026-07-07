"""
Tree-sitter language and parser management with direct parse access.
"""

import threading
from pathlib import Path
from typing import Optional

from tree_sitter import Language, Parser, Tree
from tree_sitter_language_pack import get_language

from knowledge_parser.registry import EXT_TO_LANG

LANGUAGE_ALIASES = {
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "c++": "cpp",
    "c#": "c_sharp",
    "cs": "c_sharp",
    "rb": "ruby",
    "rs": "rust",
    "go": "go",
    "php": "php",
    "kt": "kotlin",
    "scala": "scala",
    "swift": "swift",
}


class TreeSitterManager:
    def __init__(self):
        self._cache: dict[str, Language] = {}
        self._lock = threading.Lock()

    def _normalize(self, lang: str) -> str:
        return LANGUAGE_ALIASES.get(lang.lower(), lang.lower())

    def get_language(self, lang: str) -> Language:
        lang = self._normalize(lang)
        if lang in self._cache:
            return self._cache[lang]
        with self._lock:
            if lang in self._cache:
                return self._cache[lang]
            if lang == "c_sharp":
                import tree_sitter_c_sharp
                language = Language(tree_sitter_c_sharp.language())
            else:
                language = get_language(lang)
            self._cache[lang] = language
            return language

    def parse_file(self, path: Path) -> tuple[Tree, str]:
        ext = path.suffix.lower()
        lang = EXT_TO_LANG.get(ext)
        if not lang:
            raise ValueError(f"Unsupported extension: {ext}")
        language = self.get_language(lang)
        parser = Parser(language)
        source = path.read_text(encoding="utf-8")
        return parser.parse(bytes(source, "utf-8")), source

    def parse_code(self, code: str, lang: str) -> Tree:
        language = self.get_language(lang)
        return Parser(language).parse(bytes(code, "utf-8"))


_manager: Optional[TreeSitterManager] = None


def get_manager() -> TreeSitterManager:
    global _manager
    if _manager is not None:
        return _manager
    with threading.Lock():
        if _manager is None:
            _manager = TreeSitterManager()
        return _manager


def execute_query(language: Language, query_string: str, node):
    from tree_sitter import Query, QueryCursor

    try:
        query = Query(language, query_string)
        cursor = QueryCursor(query)
        captures = []
        for pattern_index, captures_dict in cursor.matches(node):
            for capture_name, nodes in captures_dict.items():
                for captured_node in nodes:
                    captures.append((captured_node, capture_name))
        return captures
    except Exception as e:
        raise Exception(f"Failed to execute query: {e}\nQuery: {query_string[:100]}...")
