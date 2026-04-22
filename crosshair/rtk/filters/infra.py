"""Infra/container filters: docker ps, docker images, docker logs."""

from __future__ import annotations

from collections import Counter

from crosshair.rtk.filters.base import (
    FilterContext,
    FilterResult,
    passthrough,
    run_subprocess,
    truncate_lines,
    which,
)


def docker_ps_filter(argv: list[str], ctx: FilterContext) -> FilterResult:
    """`docker ps` → one line per container: `<name> <image> <status>`."""
    exe = which("docker") or "docker"
    cmd = [
        exe, "ps",
        "--format",
        "{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}",
        *[a for a in argv if not a.startswith("--format")],
    ]
    proc = run_subprocess(cmd, ctx)
    raw = proc.stdout + proc.stderr
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    if not lines:
        out = "docker ps: no running containers\n"
    else:
        parts = [f"{len(lines)} container(s)"]
        for line in lines[:30]:
            name, image, status, *ports = line.split("\t") + ["", "", "", ""]
            ports = ports[0] if ports else ""
            parts.append(f"  {name}  image={image.split('@')[0]}  {status}  ports={ports or '-'}")
        if len(lines) > 30:
            parts.append(f"  … {len(lines) - 30} more")
        out = "\n".join(parts) + "\n"

    return FilterResult(
        stdout=out,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        original_chars=len(raw),
        filtered_chars=len(out),
        filter_name="docker_ps",
    )


def docker_images_filter(argv: list[str], ctx: FilterContext) -> FilterResult:
    """`docker images` → compact `repo:tag size` list."""
    exe = which("docker") or "docker"
    cmd = [
        exe, "images",
        "--format",
        "{{.Repository}}:{{.Tag}}\t{{.Size}}",
        *[a for a in argv if not a.startswith("--format")],
    ]
    proc = run_subprocess(cmd, ctx)
    raw = proc.stdout + proc.stderr
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    if not lines:
        out = "docker images: none\n"
    else:
        parts = [f"{len(lines)} image(s)"]
        for line in lines[:30]:
            try:
                repo_tag, size = line.split("\t", 1)
            except ValueError:
                continue
            parts.append(f"  {repo_tag}  {size}")
        if len(lines) > 30:
            parts.append(f"  … {len(lines) - 30} more")
        out = "\n".join(parts) + "\n"
    return FilterResult(
        stdout=out,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        original_chars=len(raw),
        filtered_chars=len(out),
        filter_name="docker_images",
    )


def docker_logs_filter(argv: list[str], ctx: FilterContext) -> FilterResult:
    """`docker logs` → dedup adjacent repeats, tail the last 200 lines."""
    exe = which("docker") or "docker"
    # If user asked for --follow, we can't safely stream — passthrough.
    if any(a in ("-f", "--follow") for a in argv):
        return passthrough(["docker", "logs", *argv], ctx)

    args = list(argv)
    if not any(a.startswith("--tail") for a in args):
        args.extend(["--tail", "500"])
    cmd = [exe, "logs", *args]
    proc = run_subprocess(cmd, ctx)
    raw = proc.stdout + proc.stderr

    out_lines: list[str] = []
    counter: Counter[str] = Counter()
    last = None
    run = 0
    for line in raw.splitlines():
        key = line.strip()
        counter[key] += 1
        if key == last:
            run += 1
            continue
        if last is not None and run > 0:
            out_lines.append(f"… (repeated ×{run + 1})")
        out_lines.append(line)
        last = key
        run = 0
    if last is not None and run > 0:
        out_lines.append(f"… (repeated ×{run + 1})")

    max_lines = ctx.max_lines or 200
    if len(out_lines) > max_lines:
        out_lines = [f"… {len(out_lines) - max_lines} earlier line(s) omitted"] + out_lines[-max_lines:]
    out = "\n".join(out_lines) + "\n"
    return FilterResult(
        stdout=out,
        stderr="",
        exit_code=proc.returncode,
        original_chars=len(raw),
        filtered_chars=len(out),
        filter_name="docker_logs",
    )
