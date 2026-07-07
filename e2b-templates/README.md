# E2B Sandbox Templates for BugViper

## Build Templates

```bash
cd e2b-templates

# Build small template (2 vCPU, 2GB RAM)
python build_small.py

# Build large template (4 vCPU, 4GB RAM)
python build_large.py
```

## Add to .env

```bash
E2B_SANDBOX_TEMPLATE_SMALL=bugviper-small
E2B_SANDBOX_TEMPLATE_LARGE=bugviper-large
```

## Template Behavior

- **Small (<=5 files)**: Uses `bugviper-small` - 2 vCPU template
- **Large (>5 files)**: Uses `bugviper-large` - 4 vCPU template
