#!/usr/bin/env python3
"""Quick smoke test for deployed admin and public routes."""

import argparse
import sys
import urllib.request
import urllib.error

ROUTES_PUBLIC: list[tuple[str, int]] = [
    ("/api/auth/status", 200),
    ("/api/generic-config", 200),
]

ROUTES_ADMIN: list[tuple[str, int]] = [
    ("/api/admin/overview", 401),
    ("/api/admin/readiness", 401),
    ("/api/admin/users", 401),
    ("/api/admin/orgs", 401),
    ("/api/admin/configs", 401),
    ("/api/admin/audit-logs", 401),
    ("/api/admin/provider-configs", 401),
    ("/api/admin/stats", 401),
    ("/api/admin/health", 401),
    ("/api/admin/exports/users", 401),
    ("/api/admin/exports/orgs", 401),
    ("/api/admin/exports/configs", 401),
    ("/api/admin/exports/audit-logs", 401),
]


def check_route(
    base_url: str,
    path: str,
    expected: int,
    cookie: str = "",
) -> tuple[str, int, int | str, str]:
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(url)
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            actual = resp.status
    except urllib.error.HTTPError as e:
        actual = e.code
    except urllib.error.URLError as e:
        return (path, -1, str(e.reason), "FAIL")

    result = "PASS" if actual == expected else "FAIL"
    if 500 <= actual < 600:
        result = "FAIL"
    return (path, actual, expected, result)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check deployed admin/public routes")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Base URL")
    parser.add_argument("--cookie", default="", help="Session cookie (optional)")
    args = parser.parse_args()

    routes_to_check: list[tuple[str, int]] = []
    routes_to_check.extend(ROUTES_PUBLIC)
    routes_to_check.extend(ROUTES_ADMIN)

    # If cookie provided, admin routes should return 200
    if args.cookie:
        for i, (path, _) in enumerate(ROUTES_ADMIN):
            routes_to_check[routes_to_check.index(ROUTES_ADMIN[i])] = (path, 200)

    header = f"{'Route':<50} {'Status':<10} {'Expected':<10} {'Result':<6}"
    print(f"Checking: {args.base_url}")
    print()
    print(header)
    print("-" * len(header))

    failures = 0
    for path, expected in routes_to_check:
        route, status, exp, result = check_route(args.base_url, path, expected, args.cookie)
        status_str = str(status) if status >= 0 else "ERR"
        print(f"{route:<50} {status_str:<10} {exp!s:<10} {result:<6}")
        if result == "FAIL":
            failures += 1

    print()
    if failures:
        print(f"{failures} route(s) FAILED")
    else:
        print("All routes passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
