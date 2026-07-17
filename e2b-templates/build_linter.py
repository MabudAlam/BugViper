from dotenv import load_dotenv
from e2b import Template, default_build_logger
from template_linter import template

load_dotenv()

if __name__ == "__main__":
    Template.build(
        template,
        "bugviper-linter",
        cpu_count=2,
        memory_mb=8192,
        on_build_logs=default_build_logger(),
    )
