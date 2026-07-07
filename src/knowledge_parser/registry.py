"""
Central language registry for knowledge_parser.

File-extension mappings and supported extension sets.
"""

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

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(EXT_TO_LANG)

LANG_PARSER_REGISTRY: dict[str, tuple[str, str]] = {
    "python": ("knowledge_parser.languages.python", "PythonLangTreeSitterParser"),
    "javascript": ("knowledge_parser.languages.javascript", "JavascriptLangTreeSitterParser"),
    "typescript": ("knowledge_parser.languages.typescript", "TypescriptLangTreeSitterParser"),
    "go": ("knowledge_parser.languages.go", "GoLangTreeSitterParser"),
    "java": ("knowledge_parser.languages.java", "JavaLangTreeSitterParser"),
    "rust": ("knowledge_parser.languages.rust", "RustLangTreeSitterParser"),
    "c": ("knowledge_parser.languages.c", "CLangTreeSitterParser"),
    "cpp": ("knowledge_parser.languages.cpp", "CppLangTreeSitterParser"),
    "ruby": ("knowledge_parser.languages.ruby", "RubyLangTreeSitterParser"),
    "c_sharp": ("knowledge_parser.languages.csharp", "CSharpLangTreeSitterParser"),
    "php": ("knowledge_parser.languages.php", "PhpLangTreeSitterParser"),
    "kotlin": ("knowledge_parser.languages.kotlin", "KotlinLangTreeSitterParser"),
    "scala": ("knowledge_parser.languages.scala", "ScalaLangTreeSitterParser"),
    "swift": ("knowledge_parser.languages.swift", "SwiftLangTreeSitterParser"),
    "haskell": ("knowledge_parser.languages.haskell", "HaskellLangTreeSitterParser"),
}
