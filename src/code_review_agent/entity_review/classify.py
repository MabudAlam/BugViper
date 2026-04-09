from .types import ChangeClassification, SemanticChange


def classify_change(change: SemanticChange) -> ChangeClassification:
    before = change.before_content or ""
    after = change.after_content or ""

    if not before or not after:
        return ChangeClassification.FUNCTIONAL

    if change.structural_change is False:
        return ChangeClassification.TEXT

    before_lines = [l.strip() for l in before.splitlines() if l.strip()]
    after_lines = [l.strip() for l in after.splitlines() if l.strip()]

    before_set = set(before_lines)
    after_set = set(after_lines)

    has_text = [False]
    has_syntax = [False]
    has_functional = [False]

    for line in before_set - after_set:
        _categorize_line(line, has_text, has_syntax, has_functional)

    for line in after_set - before_set:
        _categorize_line(line, has_text, has_syntax, has_functional)

    if not has_text[0] and not has_syntax[0] and not has_functional[0]:
        if before.strip() != after.strip():
            has_functional[0] = True
        else:
            has_text[0] = True

    return _combine_classification(has_text[0], has_syntax[0], has_functional[0])


def _categorize_line(
    line: str, has_text: list[bool], has_syntax: list[bool], has_functional: list[bool]
) -> None:
    if _is_comment_line(line):
        has_text[0] = True
    elif _is_syntax_line(line):
        has_syntax[0] = True
    else:
        has_functional[0] = True


def _is_comment_line(line: str) -> bool:
    return (
        line.startswith("//")
        or line.startswith("/*")
        or line.startswith("*")
        or line.startswith("///")
        or line.startswith("/**")
        or line.startswith('"""')
        or (line.startswith("#") and not line.startswith("#["))
    )


def _is_syntax_line(line: str) -> bool:
    line_stripped = line.strip()
    if line_stripped.startswith("fn "):
        return True
    if line_stripped.startswith("pub fn "):
        return True
    if line_stripped.startswith("pub(crate) fn "):
        return True
    if line_stripped.startswith("def "):
        return True
    if line_stripped.startswith("class "):
        return True
    if line_stripped.startswith("struct "):
        return True
    if line_stripped.startswith("enum "):
        return True
    if line_stripped.startswith("trait "):
        return True
    if line_stripped.startswith("impl "):
        return True
    if line_stripped.startswith("interface "):
        return True
    if line_stripped.startswith("type "):
        return True
    if line_stripped.startswith("pub struct "):
        return True
    if line_stripped.startswith("pub enum "):
        return True
    if line_stripped.startswith("pub trait "):
        return True
    if line_stripped.startswith("async fn "):
        return True
    if line_stripped.startswith("pub async fn "):
        return True
    if line_stripped.startswith("function "):
        return True
    if line_stripped.startswith("export function "):
        return True
    if line_stripped.startswith("export default "):
        return True
    if "->" in line_stripped:
        return True
    if "=> " in line_stripped:
        return True
    if ": &" in line_stripped:
        return True
    if ": Vec<" in line_stripped or ": Option<" in line_stripped or ": Result<" in line_stripped:
        return True
    return False


def _combine_classification(
    has_text: bool, has_syntax: bool, has_functional: bool
) -> ChangeClassification:
    if has_text and has_syntax and has_functional:
        return ChangeClassification.TEXT_SYNTAX_FUNCTIONAL
    if has_text and has_syntax and not has_functional:
        return ChangeClassification.TEXT_SYNTAX
    if has_text and not has_syntax and has_functional:
        return ChangeClassification.TEXT_FUNCTIONAL
    if not has_text and has_syntax and has_functional:
        return ChangeClassification.SYNTAX_FUNCTIONAL
    if has_text and not has_syntax and not has_functional:
        return ChangeClassification.TEXT
    if not has_text and has_syntax and not has_functional:
        return ChangeClassification.SYNTAX
    if not has_text and not has_syntax and has_functional:
        return ChangeClassification.FUNCTIONAL
    return ChangeClassification.TEXT
