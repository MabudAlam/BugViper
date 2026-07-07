"""
Tree-sitter based AST parser wrapper.

Provides language-specific parsers for extracting code structure.
"""

from pathlib import Path
from typing import Any, Dict

from tree_sitter import Language, Parser

from knowledge_parser.tree_sitter_manager import get_manager


class TreeSitterParser:
    """A generic parser wrapper for a specific language using tree-sitter."""

    def __init__(self, language_name: str):
        self.language_name = language_name
        self.ts_manager = get_manager()
        self.language: Language = self.ts_manager.get_language(language_name)

        try:
            self.parser = Parser(language=self.language)
        except (TypeError, AttributeError):
            try:
                self.parser = Parser()
                self.parser.language = self.language
            except AttributeError:
                self.parser = Parser()
                if hasattr(self.parser, "set_language"):
                    self.parser.set_language(self.language)
                else:
                    raise RuntimeError("Unable to set parser language with any known API")

        self.language_specific_parser = None
        self._load_language_parser()

    def _load_language_parser(self) -> None:
        """Load language-specific parser implementation."""
        if self.language_name == "python":
            from knowledge_parser.languages.python import PythonLangTreeSitterParser

            self.language_specific_parser = PythonLangTreeSitterParser(self)
        elif self.language_name == "javascript":
            from knowledge_parser.languages.javascript import JavascriptLangTreeSitterParser

            self.language_specific_parser = JavascriptLangTreeSitterParser(self)
        elif self.language_name == "go":
            from knowledge_parser.languages.go import GoLangTreeSitterParser

            self.language_specific_parser = GoLangTreeSitterParser(self)
        elif self.language_name == "typescript":
            from knowledge_parser.languages.typescript import TypescriptLangTreeSitterParser

            self.language_specific_parser = TypescriptLangTreeSitterParser(self)
        elif self.language_name == "cpp":
            from knowledge_parser.languages.cpp import CppLangTreeSitterParser

            self.language_specific_parser = CppLangTreeSitterParser(self)
        elif self.language_name == "rust":
            from knowledge_parser.languages.rust import RustLangTreeSitterParser

            self.language_specific_parser = RustLangTreeSitterParser(self)
        elif self.language_name == "c":
            from knowledge_parser.languages.c import CLangTreeSitterParser

            self.language_specific_parser = CLangTreeSitterParser(self)
        elif self.language_name == "java":
            from knowledge_parser.languages.java import JavaLangTreeSitterParser

            self.language_specific_parser = JavaLangTreeSitterParser(self)
        elif self.language_name == "ruby":
            from knowledge_parser.languages.ruby import RubyLangTreeSitterParser

            self.language_specific_parser = RubyLangTreeSitterParser(self)
        elif self.language_name == "c_sharp":
            from knowledge_parser.languages.csharp import CSharpLangTreeSitterParser

            self.language_specific_parser = CSharpLangTreeSitterParser(self)
        elif self.language_name == "php":
            from knowledge_parser.languages.php import PhpLangTreeSitterParser

            self.language_specific_parser = PhpLangTreeSitterParser(self)
        elif self.language_name == "kotlin":
            from knowledge_parser.languages.kotlin import KotlinLangTreeSitterParser

            self.language_specific_parser = KotlinLangTreeSitterParser(self)
        elif self.language_name == "scala":
            from knowledge_parser.languages.scala import ScalaLangTreeSitterParser

            self.language_specific_parser = ScalaLangTreeSitterParser(self)
        elif self.language_name == "swift":
            from knowledge_parser.languages.swift import SwiftLangTreeSitterParser

            self.language_specific_parser = SwiftLangTreeSitterParser(self)
        elif self.language_name == "haskell":
            from knowledge_parser.languages.haskell import HaskellLangTreeSitterParser

            self.language_specific_parser = HaskellLangTreeSitterParser(self)

    def parse(self, path: Path, is_dependency: bool = False, **kwargs) -> Dict[str, Any]:
        """Dispatches parsing to the language-specific parser."""
        if self.language_specific_parser:
            return self.language_specific_parser.parse(path, is_dependency, **kwargs)
        else:
            raise NotImplementedError(
                f"No language-specific parser implemented for {self.language_name}"
            )
