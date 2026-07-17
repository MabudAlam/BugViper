from e2b import Template, wait_for_timeout

_GO_VERSION = "1.26.5"
_GOLANGCILINT_VERSION = "v2.12.2"
_NODE_VERSION = "v22.23.1"

template = (
    Template()
    .from_base_image()
    .pip_install(["ruff"])
    .run_cmd(
        "curl -sSfL"
        " https://github.com/golangci/golangci-lint/releases/download/"
        f"{_GOLANGCILINT_VERSION}/golangci-lint-{_GOLANGCILINT_VERSION.lstrip('v')}-linux-amd64.tar.gz"
        " -o /tmp/golangci-lint.tar.gz"
        " && cd /tmp && tar -xzf golangci-lint.tar.gz"
        f" && sudo install -m 755 golangci-lint-{_GOLANGCILINT_VERSION.lstrip('v')}-linux-amd64/golangci-lint /usr/local/bin/"
        " && rm -rf /tmp/golangci-lint.tar.gz /tmp/golangci-lint-*"
    )
    .run_cmd(
        "curl -sSfL https://go.dev/dl/go{ver}.linux-amd64.tar.gz -o /tmp/go.tar.gz"
        " && sudo rm -rf /usr/local/go"
        " && sudo tar -C /usr/local -xzf /tmp/go.tar.gz"
        " && rm /tmp/go.tar.gz"
        " && sudo ln -sf /usr/local/go/bin/go /usr/local/bin/go"
        .format(ver=_GO_VERSION)
    )
    .run_cmd(
        "curl -sSfL https://nodejs.org/dist/{ver}/node-{ver}-linux-x64.tar.gz"
        " -o /tmp/node.tar.gz"
        " && sudo tar -C /usr/local -xzf /tmp/node.tar.gz --strip-components=1"
        " && rm /tmp/node.tar.gz"
        .format(ver=_NODE_VERSION)
    )
    .run_cmd("yarn global add eslint")
    .set_start_cmd("echo 'Linter sandbox ready'", wait_for_timeout(10_000))
)
