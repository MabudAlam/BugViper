"""
Central language registry for BugViper.

All file-extension mappings, supported extension sets, and the parser registry
live here. Every other module MUST import from this module instead of defining
its own local language list.
"""

# ---------------------------------------------------------------------------
# File extension → canonical tree-sitter language name
# This is the single source of truth for all supported languages.
# ---------------------------------------------------------------------------
EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".ipynb": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
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
    ".sc": "scala",
    ".swift": "swift",
    ".php": "php",
    ".hs": "haskell",
}

# All supported file extensions — derived from EXT_TO_LANG, no manual sync needed.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(EXT_TO_LANG)

# ---------------------------------------------------------------------------
# Language parser registry
# Maps canonical language name → (module path, class name) for lazy import.
# Used by the review pipeline and anywhere a tree-sitter parser must be loaded
# dynamically without importing ingestion_service at module level.
# ---------------------------------------------------------------------------
LANG_PARSER_REGISTRY: dict[str, tuple[str, str]] = {
    "python": ("ingestion_service.languages.python", "PythonLangTreeSitterParser"),
    "javascript": ("ingestion_service.languages.javascript", "JavascriptLangTreeSitterParser"),
    "typescript": ("ingestion_service.languages.typescript", "TypescriptLangTreeSitterParser"),
    "go": ("ingestion_service.languages.go", "GoLangTreeSitterParser"),
    "java": ("ingestion_service.languages.java", "JavaLangTreeSitterParser"),
    "rust": ("ingestion_service.languages.rust", "RustLangTreeSitterParser"),
    "c": ("ingestion_service.languages.c", "CLangTreeSitterParser"),
    "cpp": ("ingestion_service.languages.cpp", "CppLangTreeSitterParser"),
    "ruby": ("ingestion_service.languages.ruby", "RubyLangTreeSitterParser"),
    "c_sharp": ("ingestion_service.languages.csharp", "CSharpLangTreeSitterParser"),
    "php": ("ingestion_service.languages.php", "PhpLangTreeSitterParser"),
    "kotlin": ("ingestion_service.languages.kotlin", "KotlinLangTreeSitterParser"),
    "scala": ("ingestion_service.languages.scala", "ScalaLangTreeSitterParser"),
    "swift": ("ingestion_service.languages.swift", "SwiftLangTreeSitterParser"),
    "haskell": ("ingestion_service.languages.haskell", "HaskellLangTreeSitterParser"),
}
