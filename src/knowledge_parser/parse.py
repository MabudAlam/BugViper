from pathlib import Path

from tree_sitter import Parser, Tree
from tree_sitter_language_pack import get_language

EXT_TO_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".cs": "c_sharp",
    ".kt": "kotlin",
    ".scala": "scala",
    ".swift": "swift",
    ".php": "php",
    ".hs": "haskell",
}


def parse_file(path: Path) -> Tree:
    lang = EXT_TO_LANG.get(path.suffix.lower())
    if not lang:
        raise ValueError(f"Unsupported extension: {path.suffix}")
    language = get_language(lang)
    parser = Parser(language)
    source = path.read_text(encoding="utf-8")
    return parser.parse(bytes(source, "utf-8")), source


def parse_code(code: str, lang: str) -> Tree:
    language = get_language(lang)
    return Parser(language).parse(bytes(code, "utf-8"))
