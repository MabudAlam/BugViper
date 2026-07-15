import re
from pathlib import Path
from typing import Optional

SUPPORTED_EXTS = {
    '.py': 'python',
    '.js': 'javascript',
    '.ts': 'typescript',
    '.tsx': 'typescript',
    '.jsx': 'javascript',
    '.go': 'go',
    '.rs': 'rust',
    '.dart': 'dart',
    '.java': 'java',
    '.rb': 'ruby',
    '.php': 'php',
    '.c': 'c',
    '.h': 'c',
    '.cpp': 'cpp',
    '.hpp': 'cpp',
    '.cc': 'cpp',
    '.cxx': 'cpp',
    '.cs': 'c_sharp',
    '.kt': 'kotlin',
    '.kts': 'kotlin',
    '.scala': 'scala',
    '.swift': 'swift',
    '.hs': 'haskell',
    '.svelte': 'svelte',
}

IGNORE_DIRS = {
    '.git', 'node_modules', '__pycache__', '.venv', 'venv',
    'dist', 'build', '.next', 'vendor', 'target', '.pytest_cache',
    'coverage', '.nyc_output', 'eggs', '.eggs',
}

_LANGUAGES: dict = {}

PYTHON_BUILTINS = {
    'print', 'len', 'range', 'str', 'int', 'float', 'list', 'dict',
    'set', 'tuple', 'type', 'isinstance', 'hasattr', 'getattr', 'setattr',
    'super', 'enumerate', 'zip', 'map', 'filter', 'sorted', 'reversed',
    'open', 'bool', 'bytes', 'repr', 'format', 'input', 'abs', 'max', 'min',
    'sum', 'any', 'all', 'iter', 'next', 'vars', 'dir', 'id', 'hash',
}


def _get_language(lang: str):
    if lang in _LANGUAGES:
        return _LANGUAGES[lang]
    try:
        from tree_sitter import Language, Parser
        _LANGUAGES[lang] = _load_ts_language(lang, Language)
        return _LANGUAGES[lang]
    except Exception:
        return _get_language_fallback(lang)


def _load_ts_language(lang: str, Language):
    try:
        if lang == 'python':
            import tree_sitter_python as m; return Language(m.language())
        elif lang == 'javascript':
            import tree_sitter_javascript as m; return Language(m.language())
        elif lang == 'typescript':
            import tree_sitter_typescript as m; return Language(m.language_typescript())
        elif lang == 'go':
            import tree_sitter_go as m; return Language(m.language())
        elif lang == 'rust':
            import tree_sitter_rust as m; return Language(m.language())
        elif lang == 'dart':
            import tree_sitter_dart as m; return Language(m.language())
        elif lang == 'java':
            import tree_sitter_java as m; return Language(m.language())
        elif lang == 'ruby':
            import tree_sitter_ruby as m; return Language(m.language())
        elif lang == 'php':
            import tree_sitter_php as m; return Language(m.language_php())
        elif lang == 'c':
            import tree_sitter_c as m; return Language(m.language())
        elif lang == 'cpp':
            import tree_sitter_cpp as m; return Language(m.language())
        elif lang == 'c_sharp':
            import tree_sitter_c_sharp as m; return Language(m.language())
        elif lang == 'svelte':
            import tree_sitter_svelte as m; return Language(m.language())
        elif lang == 'kotlin':
            import tree_sitter_kotlin as m; return Language(m.language())
        elif lang == 'scala':
            import tree_sitter_scala as m; return Language(m.language())
        elif lang == 'swift':
            import tree_sitter_swift as m; return Language(m.language())
        elif lang == 'haskell':
            import tree_sitter_haskell as m; return Language(m.language())
    except Exception:
        return None
    return None


def _get_language_fallback(lang: str):
    try:
        from tree_sitter_language_pack import get_language
        _LANGUAGES[lang] = get_language(lang)
        return _LANGUAGES[lang]
    except Exception:
        return None


def parse_file(file_info: dict) -> dict:
    lang = file_info['language']
    language = _get_language(lang)

    if language is None:
        return _regex_parse(file_info)

    try:
        from tree_sitter import Parser
        parser = Parser(language)
        source = file_info['content'].encode('utf-8')
        tree = parser.parse(source)

        if lang == 'svelte':
            return _parse_svelte(tree, source, lang)

        return _walk_tree(tree, source, lang)
    except Exception:
        return _regex_parse(file_info)


def _parse_svelte(tree, source: bytes, lang: str) -> dict:
    script_node = _find_descendant(tree.root_node, 'raw_text')
    if not script_node:
        return {'functions': [], 'classes': [], 'imports': []}

    script_source = source[script_node.start_byte:script_node.end_byte]
    line_offset = source[:script_node.start_byte].count(b'\n')

    js_lang = _get_language('typescript')
    if js_lang is None:
        raw = _regex_parse_file_content(script_source.decode('utf-8', errors='ignore'), 'typescript')
        _offset_lines(raw, line_offset)
        return raw

    try:
        from tree_sitter import Parser
        js_parser = Parser(js_lang)
        js_tree = js_parser.parse(script_source)
        result = _walk_tree(js_tree, script_source, 'typescript')
        _offset_lines(result, line_offset)
        return result
    except Exception:
        raw = _regex_parse_file_content(script_source.decode('utf-8', errors='ignore'), 'typescript')
        _offset_lines(raw, line_offset)
        return raw


def _offset_lines(result: dict, offset: int):
    for fn in result.get('functions', []):
        fn['line_start'] += offset
        fn['line_end'] += offset
    for cls in result.get('classes', []):
        cls['line_start'] += offset
        cls['line_end'] += offset


def _find_descendant(node, target_type: str):
    if node.type == target_type:
        return node
    for child in node.children:
        r = _find_descendant(child, target_type)
        if r:
            return r
    return None


def _walk_tree(tree, source: bytes, lang: str) -> dict:
    functions = []
    classes = []
    imports = []

    def get_text(node):
        return source[node.start_byte:node.end_byte].decode('utf-8', errors='ignore')

    def _add_function(name: str, body_node, line_start: int, line_end: int, parent_class=None):
        body = get_text(body_node)
        preview_lines = body.split('\n')[:10]
        functions.append({
            'name': name,
            'line_start': line_start,
            'line_end': line_end,
            'body': body,
            'preview': '\n'.join(preview_lines),
            'parent_class': parent_class,
            'calls': extract_calls(body, lang),
        })

    def _function_from_value(value_node):
        """Check if a value node (or its descendants) contains an arrow/function expression."""
        if value_node is None:
            return None
        if value_node.type in ('arrow_function', 'function_expression'):
            return value_node
        for child in value_node.children:
            result = _function_from_value(child)
            if result:
                return result
        return None

    def walk(node, parent_class=None):
        # Named function declarations
        if node.type in (
            'function_definition', 'async_function_definition',
            'function_declaration', 'method_definition',
            'function_item',
            'func_literal', 'function_declaration',
        ):
            name_node = node.child_by_field_name('name')
            if name_node:
                _add_function(
                    get_text(name_node), node,
                    node.start_point[0] + 1, node.end_point[0] + 1,
                    parent_class,
                )

        # Arrow functions and function expressions assigned to variables
        # e.g., const fn = (...) => {...} or const fn = query({handler: fn})
        elif node.type == 'variable_declarator':
            name_node = node.child_by_field_name('name')
            value_node = node.child_by_field_name('value')
            if name_node and value_node:
                fn_body = _function_from_value(value_node)
                if fn_body:
                    _add_function(
                        get_text(name_node), fn_body,
                        node.start_point[0] + 1, node.end_point[0] + 1,
                        parent_class,
                    )

        elif node.type in (
            'class_definition',
            'class_declaration',
            'struct_item',
            'type_declaration',
            'impl_item',
        ):
            name_node = node.child_by_field_name('name')
            if name_node:
                class_name = get_text(name_node)
                parents = []
                if node.type == 'class_definition':
                    bases = node.child_by_field_name('superclasses') or node.child_by_field_name('bases')
                    if bases:
                        for child in bases.children:
                            if child.type not in (',', '(', ')'):
                                parents.append(get_text(child).strip())
                elif node.type == 'class_declaration':
                    for child in node.children:
                        if child.type == 'class_heritage':
                            for sub in child.children:
                                if sub.type == 'identifier':
                                    parents.append(get_text(sub))

                classes.append({
                    'name': class_name,
                    'line_start': node.start_point[0] + 1,
                    'line_end': node.end_point[0] + 1,
                    'parents': parents,
                })
                for child in node.children:
                    walk(child, parent_class=class_name)
                return

        elif node.type in (
            'import_statement', 'import_from_statement',
            'import_declaration', 'export_statement',
            'use_declaration',
            'import_spec',
        ):
            imp = extract_import(node, lang, get_text)
            if imp:
                imports.append(imp)

        for child in node.children:
            walk(child, parent_class)

    walk(tree.root_node)
    return {
        'functions': functions,
        'classes': classes,
        'imports': list(set(i for i in imports if i)),
    }


def _extract_function_body(content: str, start_line: int, lang: str) -> str:
    lines = content.split('\n')
    if start_line < 1:
        return ''
    depth = 0
    started = False
    body_lines = []
    for i, line in enumerate(lines[start_line - 1:], start=start_line):
        stripped = line.strip()
        if not started:
            if '{' in stripped or ':' in stripped:
                started = True
            if not started:
                continue
        if lang == 'python':
            if stripped and not stripped.startswith((' ', '\t', '#', '"""', "'''")) and i > start_line:
                break
        body_lines.append(line)
        depth += stripped.count('{') - stripped.count('}')
        if depth <= 0 and started and i > start_line:
            break
    return '\n'.join(body_lines)


def _regex_parse(file_info: dict) -> dict:
    return _regex_parse_file_content(file_info['content'], file_info['language'])


def _regex_parse_file_content(content: str, lang: str) -> dict:
    functions = []
    classes = []
    imports = []

    if lang == 'python':
        for m in re.finditer(r'^(?:async\s+)?def\s+(\w+)\s*\(', content, re.MULTILINE):
            line = content[:m.start()].count('\n') + 1
            body = _extract_function_body(content, line, 'python')
            functions.append({
                'name': m.group(1), 'line_start': line, 'line_end': line + body.count('\n'),
                'body': body, 'preview': body[:200], 'parent_class': None,
                'calls': extract_calls(body, 'python'),
            })
        for m in re.finditer(r'^class\s+(\w+)\s*(?:\(([^)]*)\))?', content, re.MULTILINE):
            line = content[:m.start()].count('\n') + 1
            parents = [p.strip() for p in m.group(2).split(',')] if m.group(2) else []
            parents = [p for p in parents if p and p != 'object']
            classes.append({'name': m.group(1), 'line_start': line, 'line_end': line + 10, 'parents': parents})
        for m in re.finditer(r'^(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))', content, re.MULTILINE):
            mod = (m.group(1) or m.group(2)).split('.')[0]
            imports.append(mod)

    elif lang in ('javascript', 'typescript', 'dart', 'svelte'):
        for m in re.finditer(r'(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\()', content):
            name = m.group(1) or m.group(2)
            if name:
                line = content[:m.start()].count('\n') + 1
                body = _extract_function_body(content, line, 'ts')
                functions.append({
                    'name': name, 'line_start': line, 'line_end': line + body.count('\n'),
                    'body': body, 'preview': body[:200], 'parent_class': None,
                    'calls': extract_calls(body, lang),
                })
        for m in re.finditer(r'class\s+(\w+)(?:\s+extends\s+(\w+))?', content):
            line = content[:m.start()].count('\n') + 1
            parents = [m.group(2)] if m.group(2) else []
            classes.append({'name': m.group(1), 'line_start': line, 'line_end': line + 10, 'parents': parents})
        for m in re.finditer(r"(?:import|require)\s*\(?['\"]([^'\"]+)['\"]", content):
            pkg = m.group(1)
            if not pkg.startswith('.'):
                imports.append(pkg.split('/')[0])

    elif lang in ('go', 'java', 'kotlin', 'scala'):
        for m in re.finditer(r'(?:func\s+(\w+)|public\s+(?:\w+\s+)*(\w+)\s*\()', content):
            name = m.group(1) or m.group(2)
            if name:
                line = content[:m.start()].count('\n') + 1
                body = _extract_function_body(content, line, 'c')
                functions.append({
                    'name': name, 'line_start': line, 'line_end': line + body.count('\n'),
                    'body': body, 'preview': body[:200], 'parent_class': None,
                    'calls': extract_calls(body, lang),
                })
        for m in re.finditer(r'class\s+(\w+)', content):
            line = content[:m.start()].count('\n') + 1
            classes.append({'name': m.group(1), 'line_start': line, 'line_end': line + 10, 'parents': []})
        for m in re.finditer(r'(?:import|package)\s+([\w.]+)', content):
            mod = m.group(1).split('.')[0]
            imports.append(mod)

    elif lang in ('ruby',):
        for m in re.finditer(r'(?:def\s+(\w+)|class\s+(\w+))', content):
            name = m.group(1) or m.group(2)
            if name:
                line = content[:m.start()].count('\n') + 1
                body = _extract_function_body(content, line, 'ruby')
                functions.append({
                    'name': name, 'line_start': line, 'line_end': line + body.count('\n'),
                    'body': body, 'preview': body[:200], 'parent_class': None,
                    'calls': extract_calls(body, lang),
                })
        for m in re.finditer(r'require\s+[\'"]([^\'"]+)[\'"]', content):
            imports.append(m.group(1))

    elif lang in ('c', 'cpp', 'c_sharp'):
        for m in re.finditer(r'\b(\w+)\s*\([^)]*\)\s*\{', content[:5000]):
            name = m.group(1)
            if name and not name.startswith(('if', 'for', 'while', 'switch', 'catch')):
                line = content[:m.start()].count('\n') + 1
                body = _extract_function_body(content, line, 'c')
                functions.append({
                    'name': name, 'line_start': line, 'line_end': line + body.count('\n'),
                    'body': body, 'preview': body[:200], 'parent_class': None,
                    'calls': extract_calls(body, lang),
                })
        for m in re.finditer(r'#include\s*[<\"]([^>\"]+)[>\"]', content):
            imports.append(m.group(1))

    return {
        'functions': functions,
        'classes': classes,
        'imports': list(set(imports)),
    }


def extract_calls(body: str, lang: str) -> list:
    if lang in ('javascript', 'typescript', 'dart', 'svelte'):
        calls = re.findall(r'(?:[a-zA-Z_$][\w$]*\s*\.\s*)*[a-zA-Z_$][\w$]*\s*\(', body)
        cleaned = set()
        keywords = {'if', 'for', 'while', 'switch', 'catch', 'function', 'return',
                     'throw', 'new', 'delete', 'typeof', 'instanceof', 'void',
                     'await', 'yield', 'case', 'import', 'export', 'from', 'require',
                     'async'}
        for c in calls:
            c = c.strip()[:-1].strip()
            base = c.rsplit('.', 1)[-1]
            if base not in keywords and len(base) > 1:
                cleaned.add(c)
        return list(cleaned)
    elif lang == 'python':
        calls = re.findall(r'(?:\w+\.)*[a-zA-Z_]\w*\s*\(', body)
        cleaned = set()
        for c in calls:
            c = c.strip()[:-1].strip()
            base = c.rsplit('.', 1)[-1]
            if base not in PYTHON_BUILTINS and len(base) > 1:
                cleaned.add(c)
        return list(cleaned)
    elif lang in ('go', 'java', 'ruby', 'php', 'c', 'cpp', 'c_sharp', 'kotlin', 'scala', 'swift', 'haskell'):
        calls = re.findall(r'(?:\w+\.)*[a-zA-Z_]\w*\s*\(', body)
        cleaned = set()
        for c in calls:
            c = c.strip()[:-1].strip()
            base = c.rsplit('.', 1)[-1]
            if len(base) > 1:
                cleaned.add(c)
        return list(cleaned)
    return []


def extract_import(node, lang: str, get_text) -> Optional[str]:
    text = get_text(node)
    if lang == 'python':
        m = re.match(r'from\s+([\w.]+)\s+import|import\s+([\w.]+)', text)
        if m:
            return (m.group(1) or m.group(2)).split('.')[0]
    elif lang in ('javascript', 'typescript', 'svelte'):
        m = re.search(r"from\s+['\"]([^'\"]+)['\"]|require\(['\"]([^'\"]+)['\"]\)", text)
        if m:
            pkg = m.group(1) or m.group(2)
            if not pkg.startswith('.'):
                return pkg.split('/')[0].lstrip('@')
    elif lang == 'rust':
        m = re.match(r'use\s+([\w:]+)', text)
        if m:
            return m.group(1).split('::')[0]
    elif lang == 'go':
        m = re.search(r'"([^"]+)"', text)
        if m:
            parts = m.group(1).split('/')
            if len(parts) > 1:
                return parts[-1]
    return None


def get_source_files(repo_path: str) -> list[dict]:
    files = []
    for root, dirs, filenames in Path(repo_path).walk():
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith('.')]
        for fname in filenames:
            ext = Path(fname).suffix
            if ext in SUPPORTED_EXTS:
                fpath = Path(root) / fname
                rel_path = str(fpath.relative_to(repo_path))
                try:
                    content = fpath.read_text(encoding='utf-8', errors='ignore')
                    if 5 < len(content) < 500_000:
                        files.append({
                            'path': rel_path,
                            'language': SUPPORTED_EXTS[ext],
                            'content': content,
                            'lines': content.count('\n') + 1,
                        })
                except Exception:
                    pass
    return files


def parse_source_files(repo_path: str) -> tuple[list[dict], list[dict]]:
    files = get_source_files(repo_path)
    parsed = [parse_file(f) for f in files]
    return files, parsed
