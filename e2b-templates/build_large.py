from dotenv import load_dotenv
from e2b import Template, default_build_logger
from template_large import template

load_dotenv()

if __name__ == "__main__":
    Template.build(
        template,
        "bugviper-large",  # Use: E2B_SANDBOX_TEMPLATE_LARGE=bugviper-large
        cpu_count=4,
        memory_mb=4096,
        on_build_logs=default_build_logger(),
    )
