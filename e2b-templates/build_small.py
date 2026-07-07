from dotenv import load_dotenv
from e2b import Template, default_build_logger
from template_small import template

load_dotenv()

if __name__ == "__main__":
    Template.build(
        template,
        "bugviper-small",  # Use: E2B_SANDBOX_TEMPLATE_SMALL=bugviper-small
        cpu_count=2,
        memory_mb=2048,
        on_build_logs=default_build_logger(),
    )
