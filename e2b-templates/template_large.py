from e2b import Template, wait_for_timeout

template = (
    Template()
    .from_base_image()
    .set_start_cmd("echo 'Large template ready'", wait_for_timeout(5_000))
)
