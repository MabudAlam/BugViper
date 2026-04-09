import os
import re

from tree_sitter import Parser

from common.tree_sitter_manager import create_parser
from .types import ChangeType, SemanticChange


SUPPORTED_EXTENSIONS = {
    ".rs",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".py",
    ".go",
    ".java",
    ".c",
    ".cpp",
    ".rb",
    ".cs",
    ".php",
    ".swift",
    ".kt",
    ".sh",
    ".bash",
    ".tf",
    ".hcl",
    ".vue",
}

LANGUAGE_MAP = {
    ".rs": "rust",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "jsx",
    ".py": "python",
    ".go": "golang",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".rb": "ruby",
    ".cs": "c_sharp",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".sh": "bash",
    ".bash": "bash",
    ".tf": "hcl",
    ".hcl": "hcl",
    ".vue": "vue",
}

ENTITY_KINDS = {
    "function",
    "method",
    "class",
    "struct",
    "interface",
    "enum",
    "trait",
    "impl",
}


class EntityExtractor:
    def __init__(self, file_path: str, content: str):
        self.file_path = file_path
        self.content = content
        self.lang = self._get_language()
        self._parser = None

    def _get_language(self) -> str | None:
        _, ext = os.path.splitext(self.file_path)
        return LANGUAGE_MAP.get(ext.lower())

    def _get_parser(self) -> Parser | None:
        if not self.lang:
            return None
        if self._parser is None:
            self._parser = create_parser(self.lang)
        return self._parser

    def extract_entities(self) -> list[dict]:
        parser = self._get_parser()
        if not parser:
            return []

        try:
            tree = parser.parse(bytes(self.content, "utf8"))
        except Exception:
            return []

        entities = []
        for node in tree.root_node.named_children:
            entity_type = self._get_entity_type(node.type)
            if entity_type != "unknown":
                name = self._extract_name(node, entity_type)
                entities.append(
                    {
                        "entity_id": f"{self.file_path}::{entity_type}::{name}",
                        "entity_name": name,
                        "entity_type": entity_type,
                        "file_path": self.file_path,
                        "start_line": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                        "content": node.text.decode()
                        if hasattr(node.text, "decode")
                        else str(node.text),
                    }
                )
        return entities

    def _extract_name(self, node, entity_type: str) -> str:
        text = self._get_node_name(node)
        if entity_type in ("function", "method"):
            match = re.search(r"(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", text)
            if match:
                return match.group(1)
            match = re.match(r"(\w+)\s*\(", text)
            if match:
                return match.group(1)
        elif entity_type in ("class", "struct", "enum", "trait", "interface"):
            match = re.search(r"(?:pub\s+)?(?:struct|class|enum|trait|interface)\s+(\w+)", text)
            if match:
                return match.group(1)
        return text.split("\n")[0].strip()[:50]

    def _get_entity_type(self, node_type: str) -> str:
        type_lower = node_type.lower()
        for entity in ENTITY_KINDS:
            if entity in type_lower:
                return entity
        return "unknown"

    def _get_node_name(self, node) -> str:
        try:
            return node.text.decode()
        except Exception:
            return str(node.text)


class EntityDiffer:
    def __init__(self, before_content: str, after_content: str, file_path: str):
        self.before_content = before_content
        self.after_content = after_content
        self.file_path = file_path
        self._before_extractor = EntityExtractor(file_path, before_content)
        self._after_extractor = EntityExtractor(file_path, after_content)

    def compute_diff(self) -> list[SemanticChange]:
        before_entities = {e["entity_name"]: e for e in self._before_extractor.extract_entities()}
        after_entities = {e["entity_name"]: e for e in self._after_extractor.extract_entities()}

        changes = []
        all_names = set(before_entities.keys()) | set(after_entities.keys())

        for name in all_names:
            before = before_entities.get(name)
            after = after_entities.get(name)

            if before and after:
                if before["content"] != after["content"]:
                    change_type = ChangeType.MODIFIED
                else:
                    continue
            elif after and not before:
                change_type = ChangeType.ADDED
            elif before and not after:
                change_type = ChangeType.DELETED
            else:
                continue

            entity = after if after else before
            entity_id = entity["entity_id"]
            entity_name = entity["entity_name"]
            entity_type = entity["entity_type"]

            before_c = before["content"] if before else None
            after_c = after["content"] if after else None

            structural_change = self._detect_structural_change(before_c, after_c, change_type)

            changes.append(
                SemanticChange(
                    entity_id=entity_id,
                    entity_name=entity_name,
                    entity_type=entity_type,
                    file_path=self.file_path,
                    change_type=change_type,
                    before_content=before_c,
                    after_content=after_c,
                    structural_change=structural_change,
                    start_line=entity.get("start_line", 0),
                    end_line=entity.get("end_line", 0),
                )
            )

        return changes

    def _detect_structural_change(
        self, before: str | None, after: str | None, change_type: ChangeType
    ) -> bool | None:
        if change_type == ChangeType.ADDED:
            return None
        if change_type == ChangeType.DELETED:
            return False

        if not before or not after:
            return None

        structural_patterns = [
            r"^fn ",
            r"^pub fn ",
            r"^def ",
            r"^class ",
            r"^struct ",
            r"^enum ",
            r"^interface ",
            r"^type ",
        ]

        for pattern in structural_patterns:
            if re.match(pattern, before.strip()) or re.match(pattern, after.strip()):
                return True

        if before.strip() == after.strip():
            return False

        return None
