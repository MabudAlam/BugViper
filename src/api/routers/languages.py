"""
Language registry endpoint.

Returns the supported languages from the central registry so the frontend
stays in sync without hardcoding.
"""

from fastapi import APIRouter

from common.languages import EXT_TO_LANG, LANG_PARSER_REGISTRY

router = APIRouter()

LANG_COLOURS: dict[str, str] = {
    "python": "#3572A5",
    "javascript": "#f1e05a",
    "typescript": "#3178c6",
    "go": "#00ADD8",
    "rust": "#dea584",
    "java": "#b07219",
    "ruby": "#701516",
    "c": "#555555",
    "cpp": "#f34b7d",
    "c_sharp": "#4F5D95",
    "kotlin": "#A97BFF",
    "scala": "#c22d40",
    "swift": "#F05138",
    "php": "#4F5D95",
    "haskell": "#5e5086",
}


@router.get("/")
def get_languages() -> dict[str, list[str] | dict[str, str]]:
    supported = list(LANG_PARSER_REGISTRY.keys())
    extensions = list(EXT_TO_LANG.keys())
    colours = LANG_COLOURS

    return {
        "languages": supported,
        "extensions": extensions,
        "colours": colours,
    }
